"""Human-readable labels for raw codes and values."""

# WMO weather interpretation codes, as used by Open-Meteo
WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}

# codes that mean heavy rain or snow, or storms
SEVERE_WEATHER = {65, 67, 75, 82, 86, 95, 96, 99}


def weather_label(code):
    return WMO.get(code, "Unknown")


# US EPA AQI bands: (upper bound, label)
AQI_BANDS = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"),
             (200, "Unhealthy"), (300, "Very unhealthy")]


def aqi_label(aqi):
    if aqi is None:
        return None
    return next((name for upper, name in AQI_BANDS if aqi <= upper), "Hazardous")
