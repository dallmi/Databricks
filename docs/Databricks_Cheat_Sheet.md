# Databricks: find and export files

**Cheat sheet for locked-down environments**

Created: March 2026 | Context: Databricks on Azure

---

## Quick reference: what works

| Task | Working solution |
|---|---|
| Download a file | Copy the file to `/FileStore/`, then open the URL directly in the browser |
| Delete a file | `dbutils.fs.rm()` or `%fs rm` command |
| List files | `%fs ls /path/` or `dbutils.fs.ls()` |

---

## 1. Download a file from DBFS

**Status: WORKS (browser method)**

In a locked-down environment, `displayHTML()` and `IPython.display` do not work reliably. The most reliable method is direct browser access via the FileStore URL.

### Step 1: Copy the file to FileStore

```python
dbutils.fs.cp(
    "dbfs:/tmp/website.csv",
    "dbfs:/FileStore/website.csv",
    True  # overwrite if present
)
```

### Step 2: Build the download URL

```python
workspace_url = "https://adb-XXXXXX.Y.azuredatabricks.net"
filename = "website.csv"
download_url = f"{workspace_url}/files/{filename}"
print(download_url)
```

### Step 3: Open the URL in the browser

Just copy the printed URL into the browser address bar and hit Enter. The download starts automatically.

> **Note:** You can find the workspace URL in your browser address bar while logged into Databricks. Format: `https://adb-XXXXXX.Y.azuredatabricks.net`

---

## What did NOT work

**Status: FAILED (HTML rendering blocked)**

The following methods were tested and did not work in the locked-down environment:

1. **IPython.display.FileLink:** Shows the path only as text, no clickable link.
2. **IPython.display.HTML:** HTML is rendered as plain text, not as a link.
3. **displayHTML() (Databricks native):** Also blocked by the security policy.

Reason: The environment blocks HTML rendering in notebooks for security reasons (data exfiltration prevention).

---

## 2. Delete files in DBFS

**Status: WORKS**

### Delete a single file

```python
dbutils.fs.rm("dbfs:/FileStore/website.csv")
```

### Delete a folder recursively

```python
dbutils.fs.rm("dbfs:/tmp/", recurse=True)
```

### Alternative: %fs magic commands

```
# List contents
%fs ls /FileStore

# Delete file
%fs rm /FileStore/website.csv

# Delete folder recursively
%fs rm -r /tmp/
```

> **Warning:** Deletions are immediate and irreversible! Be careful with `recurse=True`.

---

## 3. Reusable helper function

This function combines all steps: copy the file to FileStore and print the download URL.

```python
def download_from_dbfs(dbfs_path, workspace_url):
    """
    Copies a file to /FileStore/ and prints the
    download URL that can be opened in the browser.

    Example:
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
    print(f"File copied to: {target}")
    print(f"Download URL: {url}")
    print(">> Open this URL in the browser to download <<")
    return url
```

### Usage

```python
# Define the workspace URL once
WORKSPACE = "https://adb-205203499382645.5.azuredatabricks.net"

# Download file
download_from_dbfs("dbfs:/tmp/website.csv", WORKSPACE)
```

---

## 4. DBFS File Browser (UI)

If the DBFS File Browser is available in your environment:

1. In the left sidebar click "Data"
2. Select "DBFS" (top of the menu)
3. Navigate to the target folder (e.g. `/tmp/`)
4. Click the three-dot menu next to the file
5. Select "Download"

> **Note:** In locked-down environments the DBFS File Browser may be disabled. In that case use the code method from section 1.

---

## 5. Turn screenshots into table data

If export is fully blocked, screenshots of tables can be used as a workaround:

**Option A (OneNote OCR):** Paste screenshot into OneNote, right-click > "Copy text from picture", paste into Excel.

**Option B (Python Tesseract):** Local OCR script if Python is available:

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

**Option C (AI assistant):** Send the screenshot to an AI assistant that extracts the table and returns it as CSV/Excel.
