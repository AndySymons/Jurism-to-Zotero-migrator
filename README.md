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
#### Export import 
If you Google how to migrate from Jurism to Zotero, the first answer is to export your library from Jurism to RDF, then import the RDF to a clean Zotero database. 
In my case however, Jurism gives highly cryptic error messages like 
   [JavaScript Error: "XML Parsing Error: not well-formed
   [JavaScript Error: "Unsupported library type 'undefined' for library undefined" {file: "chrome://zotero/content/xpcom/uri.js" line: 112}] 
I have no idea how to fix these! I do not even know whether there is something corrupt in my database or a bug in Jurism. There is no obvious way of finding out. 
I did find I could export individual items or small groups, but failed to find an algorithm that would export the whole library. 
In any case, this method would probably not include the multi-language information. 

#### Copy the whole database 
Copying the jurism.sqlite database from Jurism to Zotero, renaming it zotero.sqlite, and asking Zotero to read it does not work. 
Zotero recognises it as 6.0.22m4 but thinks it is for a future Zotero release, even though I am on 9.0. 
I tried reverting to Zotero 6, but that and later versions did not work. 
I tried manually editing the Version table using DB Browser for SQLite (to the last version 6 build, version 295) but was unable to trick Zotero into recognising it as an old Zotero database that just needed upgrading.
In any case, this method would also probably not include the multi-language information.

#### Directly manipulate the Jurism database
It would be theoretically possible to 'clean up' the Jurism database to strip out the additional Jurism features, but, given the experience with just changing the version, it would be easy to corrupt the database and render it useless.      

#### Insert directly into the new Zotero database
This is a variation that is theoretically possible, but would have the same danger of corrupting the database and render it useless, or worse, introduce a subtle corruption that appears good now but creates an unforeseen problem in the future. 

### The chosen solution 
The chosen solution is therefore non-invasive: 
1 Stage 1 is a custom program to extract the data from jurism.sqlite on a read-only basis - it does not change anything in jurism.sqlite 
2 Stage 2 is to import this data using a built-in Zotero import facility, so the integrity of the resulting database is secured 

A bonus feature of the custom program is that it interprets Jurism language features and translates them into Zotero tags (in the Extra field) that can be dirertly used by the CNE plugin. 

### Summary of features (of the program and the procedure)
1.	Because Zotero import changes the 'Date added' to the date of the import, the extractor preserves the ‘Original date added' in the Extra field as “original-date-added:”
2.	Storage items from Jurism/storage are transferred into Zotero/storage correctly linked to new subfolders (Zotero import creates new item id,s so the old ones do mnot work 
4.	Linked attachments present and correctly linked with a relative address to the same folder as Jurism used. This can then be changed later with a single Zotero setting. 
5.	Jurism language settings preserved as CNE tags placed in the “Extra” field.  Note 1: I only use European languages and the Latin alphabet, so settings for romanisation of other alphabets (such as Chinese) are not needed.  Note 2: this spec does not include the extraction of judicial fields. I do not have any so have no means to test them. 
6.	All original item types preserved in the same or an equivalent type (no default to ‘document’, though there are a few legitimate items of type ‘document’). 
7.	The new library can be correctly synched with Zotero.org, without creating duplicate entries (need to purge first). 

### Design notes
1. The Zotero RDF format is used for export / import because it is (apparently, according to Zotero Forums) the only one of many alternatives that includes attachments 
2. When importing, Zotero does not keep the  original ‘Date added’; it makes it the date of the import and it cannot be changed. Therefore the the original date added is stored in the Extra field. 
3.	When importing, Zotero assigns new item ids – so if merged or synched with a copy of the same library all items will be duplicated. The solution is to start with a blank database and to purge the cloud before synching. If anything is wanted, export it first, then import it again after migration, but note the comment on duplicates!).
4. The additional tables that Jurism creates cannot be imported. Instead, the information is translated into Extra field tags that the Zotero CNE plugin can use directly.  

### limitations 
I made this for my own migration. It is freely shared, but I do have the resources to develop a program that works universally for all situations. 
It is only developed tested for the features that I actually use. Feel free to fork it if you want to tune it to your environment or add more features.   

My product: 
1.	Is only tested on MacOS 26 (Tahoe). It should work on other versions of Mac OS, and in Windows with minor changes to the procedure, but I cannot guarangtee it
2.	Requires Python 3. It does not work on Python 2. 
3. Only includes provision for Latin alphabets. I have no way of testing the "Romanised" fields. 
4.	Does not include provision for judicial data. I do not use these, so have no means of testing them.

See the separate file for operating instructions.  








