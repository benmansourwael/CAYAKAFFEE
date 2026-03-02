from app import init_db
import os
os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)
os.makedirs(os.path.join('static', 'qrcodes'), exist_ok=True)
init_db()
print("Done.")
