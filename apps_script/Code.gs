// Google Apps Script — paste this into script.google.com
// Watches all "Vendor invoices" folders under 02. Clients (Shared Drive)
// and notifies the webhook when a new file arrives.

var WEBHOOK_URL = "https://YOUR_RAILWAY_URL/webhook/new-file";  // update after deploy
var WEBHOOK_SECRET = "change_this_to_a_random_string";           // must match .env
var CLIENTS_FOLDER_ID = "1jVBFeOMXbS3X8DvLdX2afh2Ult_TRT83";   // 02. Clients folder
var PROCESSED_KEY = "processedFiles";

function checkForNewFiles() {
  var props = PropertiesService.getScriptProperties();
  var processed = JSON.parse(props.getProperty(PROCESSED_KEY) || "{}");

  var newFiles = findNewFilesInVendorFolders(processed);

  newFiles.forEach(function(fileId) {
    notifyWebhook(fileId);
    processed[fileId] = new Date().toISOString();
  });

  // Prune entries older than 90 days
  var cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 90);
  Object.keys(processed).forEach(function(id) {
    if (new Date(processed[id]) < cutoff) delete processed[id];
  });

  props.setProperty(PROCESSED_KEY, JSON.stringify(processed));
}

function findNewFilesInVendorFolders(processed) {
  var found = [];

  // Search for all "Vendor invoices" folders under the Clients folder
  // Using Drive API directly since DriveApp doesn't fully support Shared Drives
  var query = "title = 'Vendor invoices' and mimeType = 'application/vnd.google-apps.folder' and trashed = false";
  var pageToken = null;

  do {
    var params = {
      q: query,
      fields: "nextPageToken, files(id, name)",
      supportsAllDrives: true,
      includeItemsFromAllDrives: true,
      corpora: "allDrives",
      pageSize: 50
    };
    if (pageToken) params.pageToken = pageToken;

    var response = Drive.Files.list(params);
    var folders = response.files || [];

    folders.forEach(function(folder) {
      // List files in each Vendor invoices folder
      // Skip if this is a Posted folder
      if (folder.name === "Posted") return;
      var fileQuery = "'" + folder.id + "' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false";
      var fileResponse = Drive.Files.list({
        q: fileQuery,
        fields: "files(id, name)",
        supportsAllDrives: true,
        includeItemsFromAllDrives: true,
      });

      (fileResponse.files || []).forEach(function(file) {
        if (!processed[file.id]) {
          found.push(file.id);
        }
      });
    });

    pageToken = response.nextPageToken;
  } while (pageToken);

  return found;
}

function notifyWebhook(fileId) {
  var payload = JSON.stringify({ fileId: fileId, secret: WEBHOOK_SECRET });
  var options = {
    method: "post",
    contentType: "application/json",
    payload: payload,
    muteHttpExceptions: true,
  };
  var response = UrlFetchApp.fetch(WEBHOOK_URL, options);
  Logger.log("File " + fileId + " → " + response.getResponseCode() + " " + response.getContentText());
}

// Run this once manually to enable Drive API and set up the time trigger
function setup() {
  // Enable Drive Advanced Service (must also be enabled in Services panel)
  Logger.log("Setting up trigger...");
  ScriptApp.newTrigger("checkForNewFiles")
    .timeBased()
    .everyMinutes(5)
    .create();
  Logger.log("Done. Checking every 5 minutes.");
}
