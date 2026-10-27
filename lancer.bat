@echo off
rem Lance le tableau de bord (navigateur sur http://localhost:8501). Ferme avec Ctrl+C.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run app\ui\app.py --server.headless false
