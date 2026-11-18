# -*- coding: utf-8 -*-
"""
Расчёт состава экобетона на 1 м³ с золой-уноса и стеклянной пылью.

Метод (упрощённый, по классической схеме подбора состава):
  1. Класс бетона выбирается по несущей способности грунта.
  2. Ограничение В/В (вода/вяжущее) — по агрессивности среды
     (влажность, сульфаты, морозные циклы).
  3. Прочность — формула Боломея–Скрамтаева:  Rb = A·Rц·(В_эф/В − 0.5),
     где В_эф = цемент + k_зола·зола + k_стекло·стекло
     (k — коэффициент эффективности пуццолановой добавки).
  4. Заполнители — по методу абсолютных объёмов (сумма объёмов = 1000 л).
  5. Считается экономия цемента и CO₂ относительно бетона без добавок
     той же прочности.

Это предварительный расчёт, НЕ замена лабораторного подбора состава и
проекта конструкции (СП 63, СП 28 / EN 206).
"""

STD_CLASSES = [7.5, 10, 12.5, 15, 20, 22.5, 25, 27.5, 30, 35, 40, 45]
MARKA = {7.5: "М100", 10: "М150", 12.5: "М150", 15: "М200", 20: "М250",
         22.5: "М300", 25: "М350", 30: "М400", 35: "М450", 40: "М550", 45: "М600"}

RHO = {"cement": 3.10, "ash": 2.30, "glass": 2.50, "sand": 2.65, "gravel": 2.70}  # кг/л
A_COEF, R_CEMENT = 0.60, 42.5      # коэффициент качества заполнителей, активность цемента, МПа
K_ASH, K_GLASS = 0.45, 0.60        # эффективность золы и молотого стекла (28 сут)
CV_FACTOR = 0.778                  # класс B = средняя прочность × 0.778 (V = 13.5 %)
BASE_WATER, SP_WATER_CUT = 185.0, 0.88   # л/м³ для П3 и снижение воды пластификатором
MIN_BINDER = 300.0
CO2_PER_KG_CEMENT = 0.85           # кг CO₂ / кг портландцемента (типично 0.8–0.9)
SAND_SHARE = 0.40                  # доля песка в смеси заполнителей


def _r(x, n=1):
    return round(x, n)


def calculate_concrete_mix(soil, climate, sulfate):
    bearing, moisture, ph = soil["bearing_capacity"], soil["moisture"], soil["ph"]
    risk = sulfate["risk"]
    ft = climate["freeze_thaw_cycles"] if climate else 0
    tmin = climate["tmin"] if climate else None
    frost = ft >= 40
    notes = []

    # 1. класс бетона по грунту
    if bearing < 1.0:
        target, soil_note = 30.0, "soil_weak"
    elif bearing < 2.0:
        target, soil_note = 25.0, "soil_mid"
    elif bearing > 4.0:
        target, soil_note = 20.0, "soil_strong"
    else:
        target, soil_note = 22.5, None

    # 2. долговечность: В/В, W, F
    wb_max, water_grade = 0.60, "W4"
    if moisture > 25:
        water_grade, wb_max = "W8", min(wb_max, 0.50)
    elif moisture > 15:
        water_grade, wb_max = "W6", min(wb_max, 0.55)
    if risk == "high":
        wb_max = min(wb_max, 0.45)
    elif risk == "moderate":
        wb_max = min(wb_max, 0.50)
    if frost:
        wb_max = min(wb_max, 0.50)
    frost_grade = "F200" if ft >= 90 else "F150" if frost else "F100"

    # 3. доли золы и стекла (от массы вяжущего)
    ash, glass = 0.20, 0.10
    if target >= 30:
        ash = 0.15                      # слабый грунт: нужен более ранний набор прочности
    if soil_note == "soil_strong":
        ash, glass = 0.25, 0.15         # массивный/плотный грунт: можно больше отходов
    if risk == "moderate":
        ash = max(ash, 0.25)            # зола повышает сульфатостойкость
    if risk == "high":
        ash = 0.30
    cap = 0.40 if risk == "high" else 0.30 if frost else 0.35
    if ash + glass > cap:
        ash = cap - glass
    glass = min(glass, 0.20)

    # 4. вода и вяжущее
    water = BASE_WATER * SP_WATER_CUT
    penalty = 0.90 if frost else 1.0    # воздухововлечение снижает прочность
    eff = 1 - ash * (1 - K_ASH) - glass * (1 - K_GLASS)
    fcm_req = target / CV_FACTOR / penalty
    beff_req = water * (fcm_req / (A_COEF * R_CEMENT) + 0.5)
    binder = max(beff_req / eff, MIN_BINDER, water / wb_max)

    cement, fa, gl = binder * (1 - ash - glass), binder * ash, binder * glass
    beff = binder * eff
    fcm = A_COEF * R_CEMENT * (beff / water - 0.5) * penalty
    char = fcm * CV_FACTOR
    cls = max([c for c in STD_CLASSES if c <= char + 1e-6] or [STD_CLASSES[0]])

    # 5. заполнители (абсолютные объёмы)
    air = 50.0 if frost else 20.0       # л/м³ воздуха
    v_binder = cement / RHO["cement"] + fa / RHO["ash"] + gl / RHO["glass"]
    v_agg = 1000 - v_binder - water - air
    mass_agg = v_agg / (SAND_SHARE / RHO["sand"] + (1 - SAND_SHARE) / RHO["gravel"])
    sand, gravel = mass_agg * SAND_SHARE, mass_agg * (1 - SAND_SHARE)

    # 6. добавки (кг на 1 м³)
    additives = [("plasticizer", binder * 0.008)]
    if frost:
        additives.append(("air", binder * 0.0005))
    if moisture > 25:
        additives.append(("waterproof", binder * 0.02))
    elif moisture > 15:
        additives.append(("waterproof", binder * 0.01))
    if ph < 5.5 or risk == "high":
        additives.append(("inhibitor", binder * 0.015))
    add_total = sum(v for _, v in additives)

    # 7. экология
    cement_saved = beff - cement        # бетон без добавок той же прочности: цемент = В_эф
    co2_saved = cement_saved * CO2_PER_KG_CEMENT

    # 8. пояснения (коды -> переводы на фронтенде)
    if soil_note:
        notes.append({"code": soil_note})
    if moisture > 25:
        notes.append({"code": "wet_very"})
    elif moisture > 15:
        notes.append({"code": "wet_mid"})
    if risk == "high":
        notes.append({"code": "sulfate_high"})
    elif risk == "moderate":
        notes.append({"code": "sulfate_mod"})
    if sulfate["source"] == "estimate" and risk != "low":
        notes.append({"code": "sulfate_est"})
    if ph < 5.5:
        notes.append({"code": "ph_acid"})
    if frost:
        notes.append({"code": "frost", "params": {"ft": ft, "f": frost_grade}})
    if tmin is not None and tmin < -25:
        notes.append({"code": "cold"})
    notes.append({"code": "scm", "params": {"a": round(ash * 100), "g": round(glass * 100)}})
    notes.append({"code": "scm_quality"})

    return {
        "cement_kg": _r(cement), "ash_kg": _r(fa), "glass_kg": _r(gl),
        "binder_kg": _r(binder), "sand_kg": _r(sand), "gravel_kg": _r(gravel),
        "water_kg": _r(water), "water_binder_ratio": _r(water / binder, 2),
        "ash_pct": round(ash * 100), "glass_pct": round(glass * 100),
        "additives": [{"code": c, "kg": _r(v, 2)} for c, v in additives],
        "additives_total_kg": _r(add_total, 2),
        "strength_class": f"B{cls:g}" + (f" ({MARKA[cls]})" if cls in MARKA else ""),
        "estimated_strength_mpa": _r(fcm),
        "frost_grade": frost_grade, "water_grade": water_grade,
        "cement_saved_kg": _r(max(cement_saved, 0)),
        "co2_saved_kg": _r(max(co2_saved, 0), 0),
        "notes": notes,
    }
