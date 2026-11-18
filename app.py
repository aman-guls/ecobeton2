# -*- coding: utf-8 -*-
"""
ЭкоБетон v2 — Flask-бэкенд с реальными данными.

  POST /api/analyze {lat, lon, sulfate_mg_kg?}
    1. SoilGrids + Open-Meteo -> реальные свойства почвы и климат
    2. geotech.py             -> тип грунта, несущая способность, риск сульфатов
    3. concrete.py            -> состав экобетона (цемент + зола + стеклянная пыль)
"""
import logging
import math
import os

from flask import Flask, jsonify, render_template, request

from concrete import calculate_concrete_mix
from geotech import assess_sulfate, build_soil
from soil_service import DataError, fetch_all

app = Flask(__name__)
log = logging.getLogger("ecobeton")

MESSAGES = {  # запасные тексты; фронтенд переводит по error_code
    "bad_coords": "Некорректные координаты.",
    "bad_sulfate": "Сульфаты должны быть числом от 0 до 100000 мг/кг.",
    "no_soil_data": "Для этой точки нет данных о почве (вода, город, ледник).",
    "rate_limited": "Сервис почвенных данных временно ограничил запросы.",
    "upstream_unavailable": "Сервис почвенных данных недоступен.",
    "internal": "Внутренняя ошибка сервера.",
}


def err(code, status):
    return jsonify({"error_code": code, "error": MESSAGES.get(code, code)}), status


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    p = request.get_json(silent=True) or {}
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (KeyError, TypeError, ValueError):
        return err("bad_coords", 400)
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > 90 or abs(lon) > 180:
        return err("bad_coords", 400)

    override = None
    if p.get("sulfate_mg_kg") not in (None, ""):
        try:
            override = float(p["sulfate_mg_kg"])
        except (TypeError, ValueError):
            return err("bad_sulfate", 400)
        if not (0 <= override <= 100000):
            return err("bad_sulfate", 400)

    try:
        raw, moisture, climate = fetch_all(lat, lon)
        soil = build_soil(raw, moisture)
        sulfate = assess_sulfate(soil, climate, override)
        mix = calculate_concrete_mix(soil, climate, sulfate)
    except DataError as e:
        return err(e.code, e.status)
    except Exception:
        log.exception("analyze failed")
        return err("internal", 500)

    warnings = []
    if soil["moisture_source"] == "estimate":
        warnings.append("moisture_estimated")
    if climate is None:
        warnings.append("climate_unavailable")

    return jsonify({
        "soil": {**soil, "lat": lat, "lon": lon},
        "climate": climate,
        "sulfate": sulfate,
        "mix": mix,
        "warnings": warnings,
    })


@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error_code": "internal", "error": "Маршрут не найден."}), 404


@app.errorhandler(500)
def server_error(_e):
    return err("internal", 500)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", host="0.0.0.0",
            port=int(os.environ.get("PORT", 5000)))
