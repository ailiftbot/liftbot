# Google Sheets Webhook Setup for Book a Demo

This guide explains how to connect the LiftBot **Book a Demo** form to a Google Sheet using Google Apps Script (free, no API key needed).

---

## Step 1: Create a Google Sheet
1. Go to [Google Sheets](https://sheets.new) and create a new spreadsheet named **LiftBot Demo Requests**.
2. In Row 1, add these column headers:
   - **A1**: `Timestamp`
   - **B1**: `First Name`
   - **C1**: `Last Name`
   - **D1**: `Work Email`
   - **E1**: `Country`
   - **F1**: `Phone`
   - **G1**: `Product`
   - **H1**: `Message`

---

## Step 2: Add Apps Script
1. In the Google Sheet, click **Extensions** > **Apps Script**.
2. Replace all existing code with:

```javascript
function doPost(e) {
  try {
    var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
    var data = JSON.parse(e.postData.contents);

    sheet.appendRow([
      data.timestamp || new Date().toISOString(),
      data.first_name || '',
      data.last_name || '',
      data.work_email || '',
      data.country || '',
      data.phone || '',
      data.product || '',
      data.message || ''
    ]);

    return ContentService.createTextOutput(
      JSON.stringify({ status: 'success' })
    ).setMimeType(ContentService.MimeType.JSON);

  } catch (error) {
    return ContentService.createTextOutput(
      JSON.stringify({ status: 'error', message: error.toString() })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}
```

3. Click **Save** (disk icon).

---

## Step 3: Deploy as Web App
1. Click **Deploy** > **New deployment**.
2. Select type: **Web app** (click the gear icon next to "Select type").
3. Configure:
   - **Description**: `LiftBot Demo Webhook`
   - **Execute as**: `Me`
   - **Who has access**: `Anyone` *(crucial so your backend can POST to it)*
4. Click **Deploy**.
5. Copy the **Web App URL** (starts with `https://script.google.com/macros/s/.../exec`).

---

## Step 4: Add URL to `.env`
In your `.env` file, paste the copied Web App URL:
```env
GOOGLE_SHEET_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec
```

Restart or reload the Django server. All new demo bookings will now automatically append a new row to your Google Sheet in real-time!
