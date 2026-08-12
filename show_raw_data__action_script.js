// Function to display the raw path of a linked attachment 
// Andrew Symons, 12-Aug-2026
// Actions and tags plugin version. Shows the path in a popup 
//
// ================================================================

// 1. Initialise the global ID tracking object if it doesn't exist
if (typeof globalThis.runningPathAuditIDs === 'undefined') {
    globalThis.runningPathAuditIDs = {};
}

let mainWin = Zotero.getMainWindow();

if (mainWin) {
    var selectedItems = Array.isArray(items) ? items : [items];

    // 2. Multi-selection rule warning (Safe from duplicate triggers)
    if (selectedItems.length > 1) {
        if (!globalThis.runningPathAuditIDs["multi-select-lock"]) {
            globalThis.runningPathAuditIDs["multi-select-lock"] = true;
            mainWin.alert("Please select only ONE item at a time.");
            globalThis.runningPathAuditIDs["multi-select-lock"] = false;
        }
    } 
    else if (selectedItems.length === 1) {
        // Explicitly extract the single item out of the array wrapper
        var singleItem = selectedItems[0];

        if (singleItem && !globalThis.runningPathAuditIDs[singleItem.id]) {
            globalThis.runningPathAuditIDs[singleItem.id] = true;

            // 3. Handle Note Items
            if (singleItem.isNote && singleItem.isNote()) {
                mainWin.alert("Notes are stored within the main database.");
            } 
            // 4. Handle Attachment Items
            else if (singleItem.isAttachment && singleItem.isAttachment()) {
                var id = singleItem.id;
                
                // Query the database directly for the raw path
                var rawPath = await Zotero.DB.valueQueryAsync(
                    "SELECT path FROM itemAttachments WHERE itemID = ?", 
                    [id]
                );
                
                if (rawPath) {
                    mainWin.alert("Raw Database Path:\n" + rawPath);
                } else {
                    mainWin.alert("No file path found for this item in the database.");
                }
            } 
            // 5. Handle Standard Reference Parent Items
            else {
                mainWin.alert("The selected item is a parent reference. Please highlight the attached file icon underneath it instead.");
            }

            // Release the item lock instantly after hitting OK
            delete globalThis.runningPathAuditIDs[singleItem.id];
        }
    }
}

