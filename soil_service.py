# -*- coding: utf-8 -*-
"""
Получение РЕАЛЬНЫХ данных по координатам (все API бесплатные, без ключей):

  * SoilGrids (ISRIC)  — глина/песок/пыль, pH, плотность, органика, крупные
                         фракции. Сетка 250 м, усреднение по слою 0–100 см.
  * Open-Meteo Forecast — влажность почвы (спутник/модель), среднее за 30 суток.
  * Open-Meteo Archive  — климат за последние 365 суток: осадки, испарение,
                         циклы заморозки–оттаивания, минимальная температура.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

HEADERS = {"User-Agent": "EcoBeton/2.0 (educational project)"}

# слои SoilGrids и их толщина (см) — для средневзвешенного значения 0–100 см
LAYERS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15, "30-60cm": 30, "60-100cm": 40}
PROPS = ["clay", "sand", "silt", "phh2o", "bdod", "soc", "cfvo"]


class DataError(Exception):
    """Ошибка получения данных; code -> переводится на фронтенде (err_<code>)."""

    def __init__(self, code, status=502):
        super().__init__(code)
        self.code = code
        self.status = status


# ---------------------------------------------------------------- кэш (6 часов)
_cache, _lock, TTL = {}, threading.Lock(), 6 * 3600


def _cached(key, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            return hit[1]
    value = fn()  # исключения не кэшируем
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (now, value)
    return value


def _get_json(url, params, retries=2, timeout=(5, 30)):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 429:
                last = "rate"
                time.sleep(2 * (i + 1))
                continue
            if r.status_code >= 500:
                last = "upstream"
                time.sleep(1 + i)
                continue
            r.raise_for_status()
            return r.json()
        except requests.Timeout:
            last = "timeout"
        except (requests.RequestException, ValueError):
            last = "upstream"
    raise DataError("rate_limited" if last == "rate" else "upstream_unavailable")


# ---------------------------------------------------------------- SoilGrids
def fetch_soilgrids(lat, lon):
    params = [("lat", lat), ("lon", lon), ("value", "mean")]
    params += [("property", p) for p in PROPS]
    params += [("depth", d) for d in LAYERS]
    js = _get_json(SOILGRIDS_URL, params, retries=2, timeout=(5, 45))

    out = {}
    for layer in (js.get("properties") or {}).get("layers", []):
        d_factor = (layer.get("unit_measure") or {}).get("d_factor") or 1
        num = den = 0.0
        for dep in layer.get("depths", []):
            v = (dep.get("values") or {}).get("mean")
            w = LAYERS.get(dep.get("label"))
            if v is None or w is None:
                continue
            num += v / d_factor * w  # переводим в «человеческие» единицы
            den += w
        out[layer["name"]] = num / den if den else None

    # единицы после деления на d_factor:
    # clay/sand/silt — %, phh2o — pH, bdod — кг/дм³, soc — г/кг, cfvo — %
    if any(out.get(k) is None for k in ("clay", "sand", "silt", "phh2o", "bdod")):
        raise DataError("no_soil_data", 404)  # вода, город, ледник и т.п.
    out["soc"] = out.get("soc") or 0.0
    out["cfvo"] = out.get("cfvo") or 0.0
    return out


# ---------------------------------------------------------------- Open-Meteo
def fetch_moisture(lat, lon):
    """Объёмная влажность слоя 9–27 см (м³/м³), среднее за последние 30 суток."""
    js = _get_json(
        METEO_FORECAST_URL,
        {"latitude": lat, "longitude": lon, "hourly": "soil_moisture_9_to_27cm",
         "past_days": 30, "forecast_days": 1, "timezone": "UTC"},
        retries=1, timeout=(5, 25),
    )
    h = js.get("hourly") or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
    vals = [v for t, v in zip(h.get("time", []), h.get("soil_moisture_9_to_27cm", []))
            if v is not None and t <= now]
    if not vals:
        raise DataError("upstream_unavailable")
    return {"mean": sum(vals) / len(vals), "current": vals[-1]}


def fetch_climate(lat, lon):
    end = datetime.now(timezone.utc).date() - timedelta(days=7)
    start = end - timedelta(days=364)
    js = _get_json(
        METEO_ARCHIVE_URL,
        {"latitude": lat, "longitude": lon, "start_date": start.isoformat(),
         "end_date": end.isoformat(), "timezone": "UTC",
         "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration"},
        retries=1, timeout=(5, 30),
    )
    d = js.get("daily") or {}
    tmax, tmin = d.get("temperature_2m_max", []), d.get("temperature_2m_min", [])
    precip, et0 = d.get("precipitation_sum", []), d.get("et0_fao_evapotranspiration", [])
    pairs = [(a, b) for a, b in zip(tmax, tmin) if a is not None and b is not None]
    if len(pairs) < 300:
        raise DataError("upstream_unavailable")

    p_sum = sum(v for v in precip if v is not None)
    e_sum = sum(v for v in et0 if v is not None)
    return {
        "precip_mm": round(p_sum),
        "et0_mm": round(e_sum),
        "aridity": round(p_sum / e_sum, 2) if e_sum > 0 else None,
        "freeze_thaw_cycles": sum(1 for a, b in pairs if b < 0 < a),
        "frost_days": sum(1 for a, b in pairs if a < 0),
        "tmin": round(min(b for _, b in pairs), 1),
    }


# ---------------------------------------------------------------- всё сразу
def fetch_all(lat, lon):
    """Возвращает (soilgrids, moisture|None, climate|None). Почва обязательна,
    влажность и климат — опциональны (при сбое приложение продолжит работу)."""
    lat, lon = round(lat, 4), round(lon, 4)
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_soil = ex.submit(_cached, ("soil", lat, lon), lambda: fetch_soilgrids(lat, lon))
        f_moist = ex.submit(_cached, ("moist", lat, lon), lambda: fetch_moisture(lat, lon))
        f_clim = ex.submit(_cached, ("clim", lat, lon), lambda: fetch_climate(lat, lon))

        soil = f_soil.result()  # DataError пойдёт наверх

        def optional(f):
            try:
                return f.result()
            except DataError:
                return None

        return soil, optional(f_moist), optional(f_clim)
