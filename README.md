# Jurism-to-Zotero-migrator
A python program to extract data and attachments from a Jurism 6 environment and import them into a Zotero 9 environment

### Why migrate? 
I have used Zotero since 2014, when I was writing my Masters dissertation. I moved to Jurism in 2020 because I needed the multilingual features, which Zotero did not have, and it worked very well for my PhD thesis, which had a lot of German and other foreign language references that I need in both languages. I had no need for, and have never used, the 'judicial' features. 
I now have around 11,000 items in the database, 4,000 linked attachments and 1,000 stored attachments, snapshots etc.        

Sadly Jurism has not been maintained since 2021. Since then ... 
- Zotero upgraded its authentication procedure for better security, and Jurism did not catch up, so Jurism can no longer synchronise using the Zotero cloud.
- I recently upgraded to a Mac Mini M4 because Microsoft 365 stopped supporting older operating systems such as Catalina and Big Sur (Mac OS 10 and 11). Jurism is not stable on Apple Silicon and keeps crashing for no apparent reason
- There is no future plan to support or upgrade Jurism.

However, in the meantime, the language features that I need are available using the CNE plugin to Zotero. This is compatible with Zotero 9 and maintained up to date. I cross my fingers this will continue, but even if it doesn't, it now seems like a better long term strategy to stick with the official Zotero. 

### Why do I need a program? (failed alternatives)
##### Export import 
If you Google how to migrate from Jurism to Zotero, the first answer is to export your library from Jurism to RDF, then import the RDF to a clean Zotero database. 
In my case however, Jurism gives highly cryptic error messages like 
   [JavaScript Error: "XML Parsing Error: not well-formed
   [JavaScript Error: "Unsupported library type 'undefined' for library undefined" {file: "chrome://zotero/content/xpcom/uri.js" line: 112}] 
I have no idea how to fix these! I do not even know whether there is something corrupt in my database or a bug in Jurism. There is no obvious way of finding out. 
I did find I could export individual items or small groups, but failed to find an algorithm that would export the whole library. 
In any case, this method would probably not include the multi-language information. 

##### Copy the whole database 
Copying the jurism.sqlite database from Jurism to Zotero, renaming it zotero.sqlite, and asking Zotero to read it does not work. 
Zotero recognises it as 6.0.22m4 but thinks it is for a future Zotero release, even though I am on 9.0. 
I tried reverting to Zotero 6, but that and later versions did not work. 
I tried manually editing the Version table using DB Browser for SQLite (to the last version 6 build, version 295) but was unable to trick Zotero into recognising it as an old Zotero database that just needed upgrading.
In any case, this method would also probably not include the multi-language information.

##### Directly manipulate the Jurism database
It would be theoretically possible to 'clean up' the Jurism database to strip out the additional Jurism features, but, given the experience with just changing the version, it would be easy to corrupt the database and render it useless.      

##### Insert directly into the new Zotero database
This is a variation that is theoretically possible, but would be similarly easy to corrupt the database and render it useless, or worse, introduce a subtle corruption that appears good now but creates an unforeseen problem in the future    
