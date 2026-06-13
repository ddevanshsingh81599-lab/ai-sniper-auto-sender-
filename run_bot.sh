#!/bin/bash
# Navigate to the project directory
cd "/Users/abhirajsingh/ai target snipper for my  invoice app"

# Activate the virtual environment
source venv/bin/activate

# Run the python script and append output to a log file
python3 main.py >> bot_cron.log 2>&1
