# Databricks: Dateien finden & exportieren

**Cheat Sheet für abgesicherte Umgebungen (Locked-Down Environment)**

Erstellt: März 2026 | Kontext: Databricks auf Azure

---

## Quick Reference: Was funktioniert

| Aufgabe | Funktionierende Lösung |
|---|---|
| Datei herunterladen | Datei nach `/FileStore/` kopieren, dann URL direkt im Browser öffnen |
| Datei löschen | `dbutils.fs.rm()` oder `%fs rm` Befehl |
| Dateien auflisten | `%fs ls /pfad/` oder `dbutils.fs.ls()` |

---

## 1. Datei aus DBFS herunterladen

**Status: FUNKTIONIERT (Browser-Methode)**

In einer abgesicherten Umgebung funktionieren `displayHTML()` und `IPython.display` nicht zuverlässig. Die zuverlässigste Methode ist der direkte Browser-Zugriff über die FileStore-URL.

### Schritt 1: Datei nach FileStore kopieren

```python
dbutils.fs.cp(
    "dbfs:/tmp/website.csv",
    "dbfs:/FileStore/website.csv",
    True  # überschreiben falls vorhanden
)
```

### Schritt 2: Download-URL zusammenbauen

```python
workspace_url = "https://adb-XXXXXX.Y.azuredatabricks.net"
dateiname = "website.csv"
download_url = f"{workspace_url}/files/{dateiname}"
print(download_url)
```

### Schritt 3: URL im Browser öffnen

Die ausgegebene URL einfach in die Browser-Adressleiste kopieren und Enter drücken. Der Download startet automatisch.

> **Hinweis:** Die Workspace-URL findest du in der Adressleiste deines Browsers, wenn du in Databricks eingeloggt bist. Format: `https://adb-XXXXXX.Y.azuredatabricks.net`

---

## Was NICHT funktioniert hat

**Status: FEHLGESCHLAGEN (HTML-Rendering blockiert)**

Folgende Methoden wurden getestet und funktionierten in der abgesicherten Umgebung nicht:

1. **IPython.display.FileLink:** Zeigt nur den Pfad als Text, keinen klickbaren Link.
2. **IPython.display.HTML:** HTML wird als Plain Text gerendert, nicht als Link.
3. **displayHTML() (Databricks-nativ):** Ebenfalls blockiert durch die Sicherheitsrichtlinien.

Grund: Die Umgebung blockiert HTML-Rendering in Notebooks aus Sicherheitsgründen (Data Exfiltration Prevention).

---

## 2. Dateien in DBFS löschen

**Status: FUNKTIONIERT**

### Einzelne Datei löschen

```python
dbutils.fs.rm("dbfs:/FileStore/website.csv")
```

### Ordner rekursiv löschen

```python
dbutils.fs.rm("dbfs:/tmp/", recurse=True)
```

### Alternative: %fs Magic Commands

```
# Inhalte auflisten
%fs ls /FileStore

# Datei löschen
%fs rm /FileStore/website.csv

# Ordner rekursiv löschen
%fs rm -r /tmp/
```

> **Achtung:** Löschungen sind sofort und unwiderruflich! Vorsicht mit `recurse=True`.

---

## 3. Wiederverwendbare Helper-Funktion

Diese Funktion kombiniert alle Schritte: Datei nach FileStore kopieren und die Download-URL ausgeben.

```python
def download_from_dbfs(dbfs_path, workspace_url):
    """
    Kopiert eine Datei nach /FileStore/ und gibt die
    Download-URL aus, die im Browser geöffnet werden kann.

    Beispiel:
        download_from_dbfs(
            "dbfs:/tmp/report.csv",
            "https://adb-XXXXXX.Y.azuredatabricks.net"
        )
    """
    import os
    filename = os.path.basename(dbfs_path)
    target = f"dbfs:/FileStore/{filename}"

    dbutils.fs.cp(dbfs_path, target, True)

    url = f"{workspace_url}/files/{filename}"
    print(f"Datei kopiert nach: {target}")
    print(f"Download-URL: {url}")
    print(">> Diese URL im Browser öffnen zum Download <<")
    return url
```

### Verwendung

```python
# Workspace-URL einmalig definieren
WORKSPACE = "https://adb-205203499382645.5.azuredatabricks.net"

# Datei herunterladen
download_from_dbfs("dbfs:/tmp/website.csv", WORKSPACE)
```

---

## 4. DBFS File Browser (UI)

Falls der DBFS File Browser in deiner Umgebung verfügbar ist:

1. Links in der Sidebar auf "Data" klicken
2. "DBFS" auswählen (oben im Menü)
3. Zum gewünschten Ordner navigieren (z.B. `/tmp/`)
4. Drei-Punkte-Menü neben der Datei klicken
5. "Download" auswählen

> **Hinweis:** In abgesicherten Umgebungen ist der DBFS File Browser möglicherweise deaktiviert. In dem Fall die Code-Methode aus Abschnitt 1 verwenden.

---

## 5. Screenshots in Tabellendaten umwandeln

Wenn Export komplett blockiert ist, können Screenshots von Tabellen als Workaround genutzt werden:

**Option A (OneNote OCR):** Screenshot in OneNote einfügen, Rechtsklick > "Text aus Bild kopieren", in Excel einfügen.

**Option B (Python Tesseract):** Lokales OCR-Script wenn Python verfügbar ist:

```python
pip install pytesseract pillow opencv-python pandas

import pytesseract, cv2, pandas as pd

image = cv2.imread("screenshot.png")
text = pytesseract.image_to_string(image)

lines = text.strip().split("\n")
data = [line.split() for line in lines if line.strip()]
df = pd.DataFrame(data)
df.to_csv("output.csv", index=False)
```

**Option C (KI-Assistent):** Screenshot an einen KI-Assistenten senden, der die Tabelle extrahiert und als CSV/Excel zurückgibt.
