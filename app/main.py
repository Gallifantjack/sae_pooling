import argparse
import webbrowser
from threading import Timer
from pathlib import Path
from dash import Dash
import dash_bootstrap_components as dbc

from app.utils.paths import get_dashboard_dir
from app.components.layout import create_layout, register_callbacks
from app.components.compare_models_plot import register_compare_models_callbacks
from app.utils.data_loader import load_available_options


def create_app(dashboard_dir: str) -> Dash:
    """Create and configure the Dash application."""
    app = Dash(
        __name__,
        external_stylesheets=[
            dbc.themes.BOOTSTRAP,
            "XXXX",
        ],
        suppress_callback_exceptions=True,  # Add this for pattern-matching callbacks
    )

    # Load available models and dataset options
    options = load_available_options(dashboard_dir)

    # Create layout with loaded models
    app.layout = create_layout(options)

    # Register all callbacks
    register_callbacks(app, options)
    register_compare_models_callbacks(app, options)

    return app


def open_browser():
    """Open the browser to the dashboard URL."""
    webbrowser.open("XXXX")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dashboard_dir",
        type=str,
        default="./output/",
        help="Path to dashboard directory",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with hot reloading",
    )
    args = parser.parse_args()

    # Get dashboard directory
    dashboard_dir = get_dashboard_dir(args.dashboard_dir)

    # Create and configure app
    app = create_app(dashboard_dir)

    # Open browser only on non-debug mode
    if not args.debug:
        Timer(1, open_browser).start()

    # Run the server
    if args.debug:
        app.run_server(
            debug=True,
            dev_tools_hot_reload=True,
            dev_tools_hot_reload_interval=0.3,
            port=8050,
        )
    else:
        app.run_server(debug=False, port=8050)


if __name__ == "__main__":
    main()
