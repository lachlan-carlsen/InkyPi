import os
from jinja2 import Template

# 1. SETUP PATHS
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RENDER_DIR = os.path.join(BASE_DIR, "render")

def generate_local_preview():
    # Load your HTML file directly
    with open(os.path.join(RENDER_DIR, "surf.html"), "r") as f:
        raw_html = f.read()

    # Remove the tags that cause the crashes
    raw_html = raw_html.replace('{% extends "plugin.html" %}', '')
    raw_html = raw_html.replace('{% block content %}', '')
    raw_html = raw_html.replace('{% endblock %}', '')

    # Create a dummy data set
    params = {
        'units': 'imperial',
        'title': 'Hanalei Bay',
        'current_date': 'Friday, Jan 9',
        'wave_height': '4.5',
        'height_unit': 'ft',
        'last_refresh_time': '14:00',
        'plugin_settings': {'displayRefreshTime': 'true'},
        'wind_info': {
            'direction': 'NNE', 'speed': '12.4', 'gust': '15.1', 'unit': 'mph'
        },
        'data_points': [
            {'label': 'Period', 'measurement': '12', 'unit': 's', 'icon': 'icons/period.png'},
            {'label': 'Avg', 'measurement': '8', 'unit': 's', 'icon': 'icons/period.png'},
            {'label': 'Direction', 'measurement': 'NW', 'unit': '', 'icon': 'icons/dir.png'},
            {'label': 'Water', 'measurement': '74', 'unit': '°F', 'icon': 'icons/temp.png'},
        ],
        # Helper to find your CSS locally
        'get_plugin_dir': lambda p: p
    }

    # Render it
    template = Template(raw_html)
    rendered_html = template.render(**params)

    # Inject the CSS manually so it definitely loads
    css_link = '<link rel="stylesheet" href="surf.css">'
    rendered_html = rendered_html.replace('<div class="surf-dashboard">', css_link + '<div class="surf-dashboard">')

    output_path = os.path.join(RENDER_DIR, "test_output.html")
    with open(output_path, "w") as f:
        f.write(rendered_html)

    print(f"✅ Preview saved to {output_path}")
    print(f"👉 Run: xdg-open {output_path}")

if __name__ == "__main__":
    generate_local_preview()
