@echo off
REM This batch file runs the Flask app in development mode
set FLASK_APP=app.py
set FLASK_ENV=development

REM Run the Flask development server
flask run
