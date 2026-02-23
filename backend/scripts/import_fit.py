import os
import sys
from datetime import timezone
import json
import fitparse
import fitdecode

# Add the backend directory to the path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal, engine, Base
from app.core.config import settings
from app.models import models

# Recreate tables
Base.metadata.create_all(bind=engine)

FIT_DIR = str(settings.FIT_IMPORT_DIR)
FAILED_REPORT_PATH = str(settings.REPORTS_DIR / "failed_fit_imports.txt")
JSON_REPORT_PATH = str(settings.REPORTS_DIR / "fit_import_report.json")

def map_sport_to_type(sport: str, sub_sport: str) -> str:
    if not sport:
        return 'other'
    sport = str(sport).lower()
    sub_sport = str(sub_sport).lower() if sub_sport else ''
    
    if sport == 'running':
        if sub_sport == 'trail':
            return 'trail'
        return 'run'
    elif sport == 'cycling':
        return 'bike'
    elif sport == 'hiking' or sub_sport == 'hiking':
        return 'hike'
    elif sport == 'generic':
        if sub_sport == 'trail':
            return 'trail'
        if sub_sport == 'road':
            return 'run'
        return 'generic'
    elif sport in ['training', 'fitness_equipment', 'strength_training']:
        return 'strength'
    elif sport in ['flexibility_training', 'yoga']:
        return 'mobility'
    return 'other'

def extract_session_data_with_fitparse(filepath: str):
    fitfile = fitparse.FitFile(filepath)
    session_msg = None
    for record in fitfile.get_messages('session'):
        session_msg = record
        break

    if not session_msg:
        raise ValueError("No session message found")

    return {
        "start_time": session_msg.get_value('start_time'),
        "total_elapsed_time": session_msg.get_value('total_elapsed_time'),
        "total_distance": session_msg.get_value('total_distance'),
        "total_ascent": session_msg.get_value('total_ascent'),
        "sport": session_msg.get_value('sport'),
        "sub_sport": session_msg.get_value('sub_sport'),
        "parser": "fitparse",
    }

def extract_session_data_with_fitdecode(filepath: str):
    session_data = {}
    with fitdecode.FitReader(filepath) as fit:
        for frame in fit:
            if isinstance(frame, fitdecode.FitDataMessage) and frame.name == 'session':
                for field in frame.fields:
                    session_data[field.name] = field.value
                break

    if not session_data:
        raise ValueError("No session message found")

    return {
        "start_time": session_data.get('start_time'),
        "total_elapsed_time": session_data.get('total_elapsed_time'),
        "total_distance": session_data.get('total_distance'),
        "total_ascent": session_data.get('total_ascent'),
        "sport": session_data.get('sport'),
        "sub_sport": session_data.get('sub_sport'),
        "parser": "fitdecode",
    }

def import_fit_files():
    db = SessionLocal()
    failed_imports = []
    imported_count = 0
    skipped_count = 0

    os.makedirs(settings.REPORTS_DIR, exist_ok=True)
    
    if not os.path.exists(FIT_DIR):
        print(f"Directory not found: {FIT_DIR}")
        return

    files = [f for f in os.listdir(FIT_DIR) if f.endswith('.fit')]
    print(f"Found {len(files)} .fit files to process.")

    for filename in files:
        filepath = os.path.join(FIT_DIR, filename)
        external_id = filename
        
        # Check if already imported
        existing = db.query(models.Session).filter(models.Session.external_id == external_id).first()
        if existing:
            print(f"Skipping {filename}, already imported.")
            skipped_count += 1
            continue
            
        try:
            try:
                session_data = extract_session_data_with_fitparse(filepath)
            except Exception:
                session_data = extract_session_data_with_fitdecode(filepath)

            start_time = session_data.get("start_time")
            total_elapsed_time = session_data.get("total_elapsed_time")
            total_distance = session_data.get("total_distance")
            total_ascent = session_data.get("total_ascent")
            sport = session_data.get("sport")
            sub_sport = session_data.get("sub_sport")
            parser_used = session_data.get("parser")
            
            if not start_time or not total_elapsed_time:
                print(f"Missing essential data in {filename}")
                continue
                
            # Ensure start_time is timezone aware (UTC)
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
                
            duration_minutes = int(total_elapsed_time / 60)
            distance_km = round(total_distance / 1000, 2) if total_distance else None
            elevation_gain_m = int(total_ascent) if total_ascent else None
            session_type = map_sport_to_type(sport, sub_sport)
            
            new_session = models.Session(
                date=start_time.date(),
                start_time=start_time,
                external_id=external_id,
                type=session_type,
                duration_minutes=duration_minutes,
                distance_km=distance_km,
                elevation_gain_m=elevation_gain_m,
                notes=f"Imported from {filename} ({parser_used})"
            )
            
            db.add(new_session)
            db.commit()
            imported_count += 1
            print(f"Imported {filename}: {session_type} on {start_time.date()} ({distance_km}km) via {parser_used}")
            
        except Exception as e:
            print(f"Error parsing {filename}: {e}")
            failed_imports.append((filename, str(e)))
            db.rollback()
            
    db.close()
    with open(FAILED_REPORT_PATH, "w", encoding="utf-8") as report:
        report.write(f"Total failed files: {len(failed_imports)}\n\n")
        for file_name, error in failed_imports:
            report.write(f"{file_name}\t{error}\n")

    json_report = {
        "source_directory": os.path.basename(os.path.normpath(FIT_DIR)),
        "total_files": len(files),
        "imported": imported_count,
        "skipped": skipped_count,
        "failed": len(failed_imports),
        "failures": [{"file": file_name, "reason": error} for file_name, error in failed_imports],
    }
    with open(JSON_REPORT_PATH, "w", encoding="utf-8") as report_json:
        json.dump(json_report, report_json, indent=2)

    print(f"Failure report written to: {FAILED_REPORT_PATH}")
    print(f"JSON report written to: {JSON_REPORT_PATH}")
    print("Import complete.")

if __name__ == "__main__":
    import_fit_files()
