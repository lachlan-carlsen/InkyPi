from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
import os
import requests
import logging
from datetime import datetime, timedelta, timezone, date
from astral import moon
import pytz
import math

logger = logging.getLogger(__name__)

# NDBC Data column indices
NDBC_COLUMNS = {
    'YY': 0, 'MM': 1, 'DD': 2, 'hh': 3, 'mm': 4,
    'WDIR': 5,   # Wind direction (degT)
    'WSPD': 6,   # Wind speed (m/s)
    'GST': 7,    # Gust speed (m/s)
    'WVHT': 8,   # Wave height (m)
    'DPD': 9,    # Dominant wave period (sec)
    'APD': 10,   # Average wave period (sec)
    'MWD': 11,   # Mean wave direction (degT)
    'PRES': 12,  # Pressure (hPa)
    'ATMP': 13,  # Air temperature (degC)
    'WTMP': 14,  # Water temperature (degC)
    'DEWP': 15,  # Dew point (degC)
    'VIS': 16,   # Visibility (nmi)
    'PTDY': 17,  # Pressure tendency (hPa)
    'TIDE': 18   # Tide (ft)
}

NDBC_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station_id}.txt"

# Buoy station info (can be expanded)
BUOY_STATIONS = {
    '51205': {'name': 'Kailua-Kona, HI', 'lat': 19.781, 'lon': -156.048},
    '51201': {'name': 'Waimea Bay, HI', 'lat': 21.673, 'lon': -158.116},
    '51202': {'name': 'Mokapu Point, HI', 'lat': 21.417, 'lon': -157.668},
    '46221': {'name': 'Santa Monica Bay, CA', 'lat': 33.854, 'lon': -118.633},
    '46253': {'name': 'San Pedro, CA', 'lat': 33.576, 'lon': -118.181},
    '41114': {'name': 'Fort Pierce, FL', 'lat': 27.551, 'lon': -80.225},
}

def get_moon_phase_name(phase_age: float) -> str:
    """Determines the name of the lunar phase based on the age of the moon."""
    PHASES_THRESHOLDS = [
        (1.0, "newmoon"),
        (7.0, "waxingcrescent"),
        (8.5, "firstquarter"),
        (14.0, "waxinggibbous"),
        (15.5, "fullmoon"),
        (22.0, "waninggibbous"),
        (23.5, "lastquarter"),
        (29.0, "waningcrescent"),
    ]
    for threshold, phase_name in PHASES_THRESHOLDS:
        if phase_age <= threshold:
            return phase_name
    return "newmoon"

def meters_to_feet(meters):
    """Convert meters to feet"""
    return meters * 3.28084

def celsius_to_fahrenheit(celsius):
    """Convert Celsius to Fahrenheit"""
    return (celsius * 9/5) + 32

def mps_to_mph(mps):
    """Convert meters per second to miles per hour"""
    return mps * 2.237

def mps_to_knots(mps):
    """Convert meters per second to knots"""
    return mps * 1.944

def degrees_to_cardinal(degrees):
    """Convert degrees to cardinal direction"""
    if degrees is None:
        return "N/A"
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int((degrees + 11.25) / 22.5) % 16
    return directions[idx]

def get_swell_quality(wave_height_ft, period_sec):
    """
    Estimate surf quality based on wave height and period.
    Longer periods generally mean cleaner, more organized swells.
    """
    if wave_height_ft is None or period_sec is None:
        return "Unknown", "?"
    
    # Period quality factor (longer = better organized swell)
    if period_sec >= 14:
        period_quality = "excellent"
    elif period_sec >= 11:
        period_quality = "good"
    elif period_sec >= 8:
        period_quality = "fair"
    else:
        period_quality = "poor"  # Short period = choppy wind swell
    
    # Size rating
    if wave_height_ft < 1:
        size_rating = "Flat"
    elif wave_height_ft < 2:
        size_rating = "Ankle"
    elif wave_height_ft < 3:
        size_rating = "Knee"
    elif wave_height_ft < 4:
        size_rating = "Waist"
    elif wave_height_ft < 5:
        size_rating = "Chest"
    elif wave_height_ft < 6:
        size_rating = "Head"
    elif wave_height_ft < 8:
        size_rating = "Overhead"
    elif wave_height_ft < 10:
        size_rating = "DOH"
    else:
        size_rating = "XXL"
    
    # Overall quality score
    if period_quality == "excellent" and wave_height_ft >= 3:
        quality = "Epic"
        score = "🔥"
    elif period_quality in ["excellent", "good"] and wave_height_ft >= 2:
        quality = "Good"
        score = "●●●●○"
    elif period_quality in ["good", "fair"] and wave_height_ft >= 1.5:
        quality = "Fair"
        score = "●●●○○"
    elif wave_height_ft >= 1:
        quality = "Poor"
        score = "●●○○○"
    else:
        quality = "Flat"
        score = "●○○○○"
    
    return quality, size_rating


class Surf(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        station_id = settings.get('stationId', '51205')
        units = settings.get('units', 'imperial')
        custom_title = settings.get('customTitle', '')
        
        timezone_str = device_config.get_config("timezone", default="Pacific/Honolulu")
        time_format = device_config.get_config("time_format", default="12h")
        tz = pytz.timezone(timezone_str)
        
        try:
            # Fetch and parse NDBC data
            ndbc_data = self.fetch_ndbc_data(station_id)
            template_params = self.parse_ndbc_data(ndbc_data, tz, units, time_format, station_id)
            
            # Set title
            if custom_title:
                template_params['title'] = custom_title
            else:
                station_info = BUOY_STATIONS.get(station_id, {})
                template_params['title'] = station_info.get('name', f'Buoy {station_id}')
            
            # Get latitude for moon phase calculation
            station_info = BUOY_STATIONS.get(station_id, {'lat': 20.0})
            lat = station_info.get('lat', 20.0)
            
            # Add moon phase data
            template_params['moon_phase'] = self.get_moon_phase_data(lat)
            
        except Exception as e:
            logger.error(f"NDBC request failed: {str(e)}")
            raise RuntimeError(f"Failed to fetch surf data: {str(e)}")
        
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        
        template_params["plugin_settings"] = settings
        template_params["units"] = units
        
        # Add last refresh time
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh_time = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh_time = now.strftime("%Y-%m-%d %I:%M %p")
        template_params["last_refresh_time"] = last_refresh_time
        
        image = self.render_image(dimensions, "surf.html", "surf.css", template_params)
        
        if not image:
            raise RuntimeError("Failed to render surf dashboard, please check logs.")
        return image

    def fetch_ndbc_data(self, station_id):
        """Fetch real-time data from NDBC buoy"""
        url = NDBC_URL.format(station_id=station_id)
        response = requests.get(url, timeout=30)
        
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to fetch NDBC data: {response.status_code}")
            raise RuntimeError(f"Failed to fetch NDBC data for station {station_id}")
        
        return response.text

    def parse_ndbc_data(self, raw_data, tz, units, time_format, station_id):
        """Parse NDBC text data into structured format"""
        lines = raw_data.strip().split('\n')
        
        # Skip header lines (start with #)
        data_lines = [line for line in lines if not line.startswith('#')]
        
        if not data_lines:
            raise RuntimeError("No data available from NDBC")
        
        # Parse readings (most recent first)
        readings = []
        for line in data_lines[:48]:  # Last 24 hours (30-min intervals)
            parts = line.split()
            if len(parts) >= 15:
                reading = self.parse_reading(parts, tz, units)
                if reading:
                    readings.append(reading)
        
        if not readings:
            raise RuntimeError("Failed to parse any valid readings from NDBC data")
        
        # Current conditions (most recent reading)
        current = readings[0]
        
        # Calculate wave height in appropriate units
        wave_height = current.get('wave_height')
        wave_height_display = wave_height if wave_height else 0
        
        # Get period and direction
        period = current.get('dominant_period')
        avg_period = current.get('average_period')
        direction = current.get('wave_direction')
        direction_cardinal = degrees_to_cardinal(direction)
        
        # Water temp
        water_temp = current.get('water_temp')
        water_temp_display = f"{water_temp:.1f}" if water_temp else "N/A"
        
        # Air temp (may not be available on all buoys)
        air_temp = current.get('air_temp')
        air_temp_display = f"{air_temp:.1f}" if air_temp else "N/A"
        
        # Get quality assessment
        quality, size_rating = get_swell_quality(wave_height, period)
        
        # Temperature unit
        temp_unit = "°F" if units == "imperial" else "°C"
        height_unit = "ft" if units == "imperial" else "m"
        
        # Build data points for the metrics grid
        data_points = []
        
        # Dominant Period
        data_points.append({
            "label": "Period",
            "measurement": f"{period:.0f}" if period else "N/A",
            "unit": "sec",
            "icon": self.get_plugin_dir('icons/period.png')
        })
        
        # Average Period
        data_points.append({
            "label": "Avg Period",
            "measurement": f"{avg_period:.1f}" if avg_period else "N/A",
            "unit": "sec",
            "icon": self.get_plugin_dir('icons/avg_period.png')
        })
        
        # Swell Direction
        data_points.append({
            "label": "Direction",
            "measurement": direction_cardinal,
            "unit": f"{direction:.0f}°" if direction else "",
            "icon": self.get_plugin_dir('icons/direction.png'),
            "arrow": self.get_direction_arrow(direction) if direction else ""
        })
        
        # Water Temperature
        data_points.append({
            "label": "Water",
            "measurement": water_temp_display,
            "unit": temp_unit,
            "icon": self.get_plugin_dir('icons/water_temp.png')
        })
        
        # Air Temperature (if available)
        if air_temp:
            data_points.append({
                "label": "Air",
                "measurement": air_temp_display,
                "unit": temp_unit,
                "icon": self.get_plugin_dir('icons/air_temp.png')
            })
        
        # Wind (if available)
        wind_speed = current.get('wind_speed')
        wind_dir = current.get('wind_direction')
        if wind_speed:
            wind_unit = "mph" if units == "imperial" else "m/s"
            data_points.append({
                "label": "Wind",
                "measurement": f"{wind_speed:.0f}",
                "unit": wind_unit,
                "icon": self.get_plugin_dir('icons/wind.png'),
                "arrow": self.get_direction_arrow(wind_dir) if wind_dir else ""
            })
        
        # Parse historical data for chart
        historical = self.parse_historical_for_chart(readings, time_format)
        
        # Get trend (comparing to 6 hours ago)
        trend = self.calculate_trend(readings)
        
        return {
            "current_date": datetime.now(tz).strftime("%A, %B %d"),
            "wave_height": f"{wave_height_display:.1f}" if wave_height_display else "0.0",
            "height_unit": height_unit,
            "quality": quality,
            "size_rating": size_rating,
            "data_points": data_points,
            "direction_degrees": direction if direction else 0,
            "direction_cardinal": direction_cardinal,
            "historical_data": historical,
            "trend": trend,
            "station_id": station_id,
            "reading_time": current.get('time_str', '')
        }

    def parse_reading(self, parts, tz, units):
        """Parse a single NDBC data line"""
        try:
            # Parse timestamp
            year = int(parts[NDBC_COLUMNS['YY']])
            month = int(parts[NDBC_COLUMNS['MM']])
            day = int(parts[NDBC_COLUMNS['DD']])
            hour = int(parts[NDBC_COLUMNS['hh']])
            minute = int(parts[NDBC_COLUMNS['mm']])
            
            dt = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            dt_local = dt.astimezone(tz)
            
            reading = {
                'datetime': dt_local,
                'time_str': dt_local.strftime("%I:%M %p")
            }
            
            # Parse wave height (convert MM to None)
            wvht = parts[NDBC_COLUMNS['WVHT']]
            if wvht != 'MM':
                wave_m = float(wvht)
                reading['wave_height'] = meters_to_feet(wave_m) if units == 'imperial' else wave_m
                reading['wave_height_m'] = wave_m
            
            # Parse dominant period
            dpd = parts[NDBC_COLUMNS['DPD']]
            if dpd != 'MM':
                reading['dominant_period'] = float(dpd)
            
            # Parse average period
            apd = parts[NDBC_COLUMNS['APD']]
            if apd != 'MM':
                reading['average_period'] = float(apd)
            
            # Parse wave direction
            mwd = parts[NDBC_COLUMNS['MWD']]
            if mwd != 'MM':
                reading['wave_direction'] = float(mwd)
            
            # Parse water temperature
            wtmp = parts[NDBC_COLUMNS['WTMP']]
            if wtmp != 'MM':
                temp_c = float(wtmp)
                reading['water_temp'] = celsius_to_fahrenheit(temp_c) if units == 'imperial' else temp_c
            
            # Parse air temperature
            atmp = parts[NDBC_COLUMNS['ATMP']]
            if atmp != 'MM':
                temp_c = float(atmp)
                reading['air_temp'] = celsius_to_fahrenheit(temp_c) if units == 'imperial' else temp_c
            
            # Parse wind speed
            wspd = parts[NDBC_COLUMNS['WSPD']]
            if wspd != 'MM':
                wind_mps = float(wspd)
                reading['wind_speed'] = mps_to_mph(wind_mps) if units == 'imperial' else wind_mps
            
            # Parse wind direction
            wdir = parts[NDBC_COLUMNS['WDIR']]
            if wdir != 'MM':
                reading['wind_direction'] = float(wdir)
            
            # Parse pressure
            pres = parts[NDBC_COLUMNS['PRES']]
            if pres != 'MM':
                reading['pressure'] = float(pres)
            
            return reading
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse NDBC reading: {e}")
            return None

    def parse_historical_for_chart(self, readings, time_format):
        """Extract historical wave heights for the chart"""
        historical = []
        
        # Get readings at regular intervals (every 2 hours for cleaner chart)
        # Readings are 30 min apart, so take every 4th
        for i, reading in enumerate(reversed(readings)):
            if i % 4 == 0:  # Every 2 hours
                dt = reading.get('datetime')
                if dt:
                    if time_format == "24h":
                        time_label = dt.strftime("%H:%M")
                    else:
                        time_label = dt.strftime("%I%p").lstrip("0").lower()
                    
                    historical.append({
                        'time': time_label,
                        'wave_height': reading.get('wave_height', 0),
                        'period': reading.get('dominant_period', 0)
                    })
        
        return historical[-12:]  # Last 24 hours worth of 2-hour intervals

    def calculate_trend(self, readings):
        """Calculate if conditions are improving, declining, or steady"""
        if len(readings) < 12:  # Need at least 6 hours of data
            return "steady"
        
        current_height = readings[0].get('wave_height', 0)
        past_height = readings[11].get('wave_height', 0)  # ~6 hours ago
        
        if current_height is None or past_height is None:
            return "steady"
        
        diff = current_height - past_height
        
        if diff > 0.5:
            return "rising"
        elif diff < -0.5:
            return "falling"
        else:
            return "steady"

    def get_direction_arrow(self, degrees):
        """Get arrow character for direction (shows where swell is coming FROM)"""
        if degrees is None:
            return ""
        
        # Arrows point in the direction the swell is traveling TO
        # So we add 180 degrees to show where it's coming from
        DIRECTIONS = [
            ("↓", 22.5),    # From N, traveling S
            ("↙", 67.5),    # From NE
            ("←", 112.5),   # From E
            ("↖", 157.5),   # From SE
            ("↑", 202.5),   # From S
            ("↗", 247.5),   # From SW
            ("→", 292.5),   # From W
            ("↘", 337.5),   # From NW
            ("↓", 360.0)    # Wrap to N
        ]
        
        degrees = degrees % 360
        for arrow, upper_bound in DIRECTIONS:
            if degrees < upper_bound:
                return arrow
        return "↓"

    def get_moon_phase_data(self, lat):
        """Calculate current moon phase"""
        today = date.today()
        
        try:
            phase_age = moon.phase(today)
            phase_name = get_moon_phase_name(phase_age)
            
            # Calculate illumination
            LUNAR_CYCLE_DAYS = 29.530588853
            phase_fraction = phase_age / LUNAR_CYCLE_DAYS
            illum_pct = (1 - math.cos(2 * math.pi * phase_fraction)) / 2 * 100
            
            # Adjust for hemisphere
            display_name = phase_name
            if lat < 0:
                if phase_name == "waxingcrescent":
                    display_name = "waningcrescent"
                elif phase_name == "waxinggibbous":
                    display_name = "waninggibbous"
                elif phase_name == "waningcrescent":
                    display_name = "waxingcrescent"
                elif phase_name == "waninggibbous":
                    display_name = "waxinggibbous"
                elif phase_name == "firstquarter":
                    display_name = "lastquarter"
                elif phase_name == "lastquarter":
                    display_name = "firstquarter"
            
            # Human-readable name
            phase_labels = {
                "newmoon": "New Moon",
                "waxingcrescent": "Waxing Crescent",
                "firstquarter": "First Quarter",
                "waxinggibbous": "Waxing Gibbous",
                "fullmoon": "Full Moon",
                "waninggibbous": "Waning Gibbous",
                "lastquarter": "Last Quarter",
                "waningcrescent": "Waning Crescent"
            }
            
            return {
                "name": phase_labels.get(phase_name, "Unknown"),
                "icon": self.get_plugin_dir(f"icons/{display_name}.png"),
                "illumination": f"{illum_pct:.0f}"
            }
            
        except Exception as e:
            logger.error(f"Error calculating moon phase: {e}")
            return {
                "name": "Unknown",
                "icon": self.get_plugin_dir("icons/fullmoon.png"),
                "illumination": "?"
            }
