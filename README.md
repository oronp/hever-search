# חיפוש הטבות חבר

אתר חיפוש עצמאי בעברית. האיסוף מושך את רשימת העסקים הציבורית של חבר, והחיפוש מתבצע בדפדפן. כל תוצאה מקשרת לעמוד ההטבה המקורי.

## עדכון האתר

GitHub Actions מריץ את האיסוף ביום ראשון בשעה 04:17 לפי שעון ישראל. אפשר להפעיל אותו גם באמצעות **Actions → Refresh and publish → Run workflow**. לאחר איסוף מוצלח, ה־workflow מפרסם גרסה חדשה ב־GitHub Pages. אם האיסוף נכשל, האתר האחרון שפורסם ממשיך להיות מוגש.

## הרצה מקומית

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install 'playwright==1.60.0' 'beautifulsoup4==4.13.4'
python -m playwright install chromium
python refresh.py --category לבית --subcategory 'עיצוב הבית'
python -m http.server 8000
```

כדי לאסוף את האינדקס כולו, מריצים `python refresh.py` בלי הסינון. תנאי המימוש ואחוזי ההנחה העדכניים זמינים בעמוד ההטבה הרשמי.
