# -*- coding: utf-8 -*-
"""
Интерпретация сырых данных: тип грунта, плотность, влажность, оценка
несущей способности и риска сульфатной агрессии.

ВАЖНО: несущая способность здесь — ОЦЕНКА по типу/плотности/влажности
(порядок величин по таблицам условных расчётных сопротивлений R0), а не
результат инженерно-геологических изысканий.
"""

# код: (базовое R0 кгс/см², эталонная плотность кг/м³, порог влажности %,
#       R0 min, R0 max, типичная влажность % — если нет данных Open-Meteo)
SOIL_MODEL = {
    "sand":       (3.0, 1600, 10, 1.5, 5.0, 8),
    "sandy_loam": (2.8, 1650, 15, 1.5, 4.0, 12),
    "loam":       (2.4, 1600, 20, 1.0, 4.0, 18),
    "clay":       (2.5, 1500, 25, 1.0, 6.0, 24),
    "gravelly":   (5.0, 1900, 8, 3.0, 8.0, 6),
    "peat":       (0.5, 150, 60, 0.2, 0.9, 60),
}


def classify_soil(clay, sand, silt, soc, cf):
    """Классификация по содержанию глинистой фракции (как в ГОСТ 25100, упрощённо)."""
    if soc >= 120:          # ≥12 % органического углерода — торф
        return "peat"
    if cf >= 50:            # >50 % крупных обломков
        return "gravelly"
    if clay >= 30:
        return "clay"
    if clay >= 10:
        return "loam"
    if clay >= 3:
        return "sandy_loam"
    return "sand" if sand >= 50 else "sandy_loam"


def build_soil(raw, moisture):
    clay, sand, silt = raw["clay"], raw["sand"], raw["silt"]
    soc, cf = raw["soc"], raw["cfvo"]
    code = classify_soil(clay, sand, silt, soc, cf)
    base, ref_rho, w0, rmin, rmax, w_default = SOIL_MODEL[code]

    # плотность сухого грунта с учётом крупных обломков (2650 кг/м³ — плотность камня)
    rho = raw["bdod"] * 1000 * (1 - cf / 100) + 2650 * cf / 100

    if moisture and moisture.get("mean") is not None:
        # объёмная влажность (м³/м³) -> массовая (%)
        w = min(80.0, moisture["mean"] * 1000 / rho * 100)
        w_src = "model"
    else:
        w, w_src = float(w_default), "estimate"

    dens_factor = max(0.8, min(1.25, rho / ref_rho))
    moist_factor = max(0.6, 1 - 0.012 * max(0.0, w - w0))
    bearing = max(rmin, min(rmax, base * dens_factor * moist_factor))

    return {
        "soil_type": code,
        "bearing_capacity": round(bearing, 2),
        "moisture": round(w, 1),
        "moisture_source": w_src,
        "density": round(rho),
        "ph": round(raw["phh2o"], 1),
        "clay": round(clay, 1),
        "silt": round(silt, 1),
        "sand": round(sand, 1),
        "organic_matter": round(soc / 10, 1),   # г/кг -> %
        "coarse_fragments": round(cf, 1),
    }


def assess_sulfate(soil, climate, override_mg_kg=None):
    """Риск сульфатной агрессии.
    Если введён лабораторный результат — классификация по ACI 318 (S0/S1/S2+):
      <1000 мг/кг — низкий, 1000–2000 — умеренный, ≥2000 — высокий.
    Иначе — косвенная оценка: засушливость + щелочность + глинистость
    (засоление и гипс типичны для аридных щелочных почв)."""
    if override_mg_kg is not None:
        risk = "low" if override_mg_kg < 1000 else "moderate" if override_mg_kg < 2000 else "high"
        return {"risk": risk, "mg_kg": round(override_mg_kg), "source": "lab"}

    score = 0
    aridity = climate.get("aridity") if climate else None
    if aridity is not None:
        score += 2 if aridity < 0.5 else 1 if aridity < 0.8 else 0
    ph = soil["ph"]
    score += 2 if ph >= 8.3 else 1 if ph >= 7.8 else 0
    if soil["clay"] >= 10:
        score += 1
    risk = "high" if score >= 4 else "moderate" if score >= 2 else "low"
    return {"risk": risk, "mg_kg": None, "source": "estimate"}
