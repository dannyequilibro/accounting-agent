// Google Apps Script — paste this into script.google.com
// Watches all "Vendor invoices" folders under 02. Clients (Shared Drive)
// and notifies the webhook when a new file arrives.

var WEBHOOK_URL = "https://web-production-4ed8d.up.railway.app/webhook/new-file";
var DIGEST_URL = "https://web-production-4ed8d.up.railway.app/digest/daily";
var WEBHOOK_SECRET = "change_this_to_a_random_string";  // must match Railway env var
var PROCESSED_KEY = "processedFiles";
var CLIENTS_FOLDER_ID = "1jVBFeOMXbS3X8DvLdX2afh2Ult_TRT83";  // 02. Clients folder

function checkForNewFiles() {
  var props = PropertiesService.getScriptProperties();
  var processed = JSON.parse(props.getProperty(PROCESSED_KEY) || "{}");

  // Returns [{folderId, fileIds: [...]}] grouped by Vendor invoices folder
  var folders = findNewFilesInVendorFolders(processed);

  folders.forEach(function(folder) {
    for (var i = 0; i < folder.fileIds.length; i++) {
      var fileId = folder.fileIds[i];
      var status = null;
      try {
        status = notifyWebhook(fileId);
      } catch(e) {
        Logger.log("Webhook error for " + fileId + ": " + e.message);
      }
      processed[fileId] = new Date().toISOString();

      // New client — sheet just created, no vendor mappings yet.
      // Don't mark remaining files as processed so they retry once mappings are added.
      if (status === "new_client") {
        Logger.log("New client detected — skipping remaining " + (folder.fileIds.length - i - 1) + " files in folder " + folder.folderId + " (will retry next run)");
        break;
      }
    }
  });

  // Prune entries older than 90 days
  var cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - 90);
  Object.keys(processed).forEach(function(id) {
    if (new Date(processed[id]) < cutoff) delete processed[id];
  });

  props.setProperty(PROCESSED_KEY, JSON.stringify(processed));
}

function listSubfolders(parentId) {
  var folders = [];
  var pageToken = null;
  do {
    var params = {
      q: "'" + parentId + "' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
      supportsAllDrives: true,
      includeItemsFromAllDrives: true,
      pageSize: 100,
      fields: "nextPageToken, files(id, name)"
    };
    if (pageToken) params.pageToken = pageToken;
    var resp = Drive.Files.list(params);
    (resp.files || []).forEach(function(f) { folders.push(f); });
    pageToken = resp.nextPageToken;
  } while (pageToken);
  return folders;
}

function findNewFilesInVendorFolders(processed) {
  var found = [];  // [{folderId, fileIds: [...]}]

  // Walk: Clients -> client folders -> outlet folders -> "Vendor invoices"
  var clientFolders = listSubfolders(CLIENTS_FOLDER_ID);

  clientFolders.forEach(function(client) {
    var outletFolders = listSubfolders(client.id);

    outletFolders.forEach(function(outlet) {
      var subfolders = listSubfolders(outlet.id);

      subfolders.forEach(function(sub) {
        if (sub.name !== "Vendor invoices") return;

        var fileResp = Drive.Files.list({
          q: "'" + sub.id + "' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false",
          supportsAllDrives: true,
          includeItemsFromAllDrives: true,
          pageSize: 100,
          fields: "files(id)"
        });

        var newFileIds = (fileResp.files || [])
          .filter(function(f) { return !processed[f.id]; })
          .map(function(f) { return f.id; });

        if (newFileIds.length > 0) {
          found.push({ folderId: sub.id, fileIds: newFileIds });
        }
      });
    });
  });

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
  var body = response.getContentText();
  Logger.log("File " + fileId + " -> " + response.getResponseCode() + " " + body);
  try {
    return JSON.parse(body).status;
  } catch(e) {
    return null;
  }
}

// Sends the daily status digest to Telegram. Fires once a day (see setup()).
// The Railway app reads the last 24h of the Run Log sheet and posts to Telegram.
function sendDailyDigest() {
  var payload = JSON.stringify({ secret: WEBHOOK_SECRET, hours: 24 });
  var options = {
    method: "post",
    contentType: "application/json",
    payload: payload,
    muteHttpExceptions: true,
  };
  var response = UrlFetchApp.fetch(DIGEST_URL, options);
  Logger.log("Digest -> " + response.getResponseCode() + " " + response.getContentText());
}

// Run this once manually to set up the time triggers
function setup() {
  ScriptApp.getProjectTriggers().forEach(function(t) { ScriptApp.deleteTrigger(t); });

  // Hourly folder check
  ScriptApp.newTrigger("checkForNewFiles")
    .timeBased()
    .everyHours(1)
    .create();

  // Daily status digest at ~7am (script timezone — set to Asia/Singapore in
  // Project Settings so this fires 7am SGT)
  ScriptApp.newTrigger("sendDailyDigest")
    .timeBased()
    .atHour(7)
    .everyDays(1)
    .create();

  Logger.log("Done. Hourly file check + daily digest at 7am.");
}
