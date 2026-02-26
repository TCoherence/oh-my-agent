---
name: weather
description: "Query current weather and forecast for a city. Use this skill when the user asks about weather conditions, temperature, or forecasts for any location."
---

# Weather Skill

Provides weather information for any city.

## Usage

Run the weather script to get current conditions:

```bash
bash skills/weather/scripts/weather.sh <city_name>
```

Example:
```bash
bash skills/weather/scripts/weather.sh "Bellevue, WA"
```

The script uses `wttr.in` — a console-friendly weather service. No API key required.

## Output Format

The script returns a concise weather summary including:
- Current temperature and conditions
- Wind speed and humidity
- 3-day forecast

Present the results in a user-friendly format with appropriate emoji.
