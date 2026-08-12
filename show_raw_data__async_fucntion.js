// Function to display the raw path of a linked attachment 
// Andrew Symons, 12-Aug-2026
// Async function version
//
// 1. In the main window, select the item for which the path is required 
// 2. Tick 'Run as async function' 
// 3. Press run 
// 4. The answer will appear to the right. "attachments:" indicates a relative path "/..." indicates an absolute path.
// ================================================================= 


var items = ZoteroPane.getSelectedItems();
if (items.length === 0) {
    return "Error: Please select a linked attachment first.";
}

var item = items[0];
var id = item.id;

// Execute the database check
var rawPath = await Zotero.DB.valueQueryAsync(
    "SELECT path FROM itemAttachments WHERE itemID = ?", 
    [id]
    );

if (!rawPath) {
    return "Error: No path found. Check if you selected a parent reference.";
}

// This goes directly to the Return Value pane when 'Run as async' is checked
return "Raw Database Path -> " + rawPath;
