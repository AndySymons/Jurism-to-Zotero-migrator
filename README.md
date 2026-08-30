# README

# Jurism-to-Zotero Migration (JZM)

Andrew Symons, 30-Aug-2026
Release 2, Draft 1 



**JZM** is a planned program to extract data and attachments from a **Jurism 6** environment and import them into a **Zotero 10** environment with the Zotero **Cite Non-English** (CNE) plugin. It will include as many of the judicial and language features of Jurism as possible.   



# — WORK IN PROGRESS —

I am now regarding version 1 (never formally released) as just a prototype or proof-of-concept. 

Version 2 is a complete rewrite with a much wider scope (so it will take me a while)…

## Summary of planned user features -- Release 2.0 

1. **Unlimited input scope** — deals with *all* Jurism judicial and language extensions as well as the standard Zotero features.  The import includes Collections, Saved Searches, and parent and Child Items of all types. 
1. **internationalised**: full support for all UI languages supported in Jurism and Zotero.
1. **Platform independent**: Will run on Mac, Windows, or Linux 
1. **’No loss’ rule**: all Jurism data goes *somewhere*. Anything not accommodated in native Zotero 10 is written to tags in the Extra field aimed at generating citations as close as possible to those by Jurism .  
1. **’No silent change’ rule**: all mappings to Zotero that are not simple 1:1 copies are recorded, both in a Migration Note attached to the Item in question, and an Anomalies report that can be browsed in the in a web browser. They both use language to match the user’s UI preference.   
1. **’All fields visible’ rule**:  To avoid unforeseen consequences, there are no hidden fields. Only fields visible to the user through the Jurism UI are mapped, and they only map to UI-visible fields in Zotero. You still have full control of your Library.    
1. **Cleans up language fields** that are recognised but not standard BCP 47; e.g. 'Spanish' is converted to 'es'. The item language field only holds the first one, but others are recorded in the Migration Notes and Anomalies Report.     
1. **Cleans up linked attachments** that have legacy absolute paths in Jurism. Redefines them as relative to the Linked Attachments Base Directory. Warns of missing files, in the Migration Notes and Anomalies Report.     
1. **Preserves the original 'Date added' and 'Modified' fields** in the 'Extra' field. Zotero changes the standard to the date and time of the import, but JZM keeps them in Extra tags.   
1. **Clear migration reporting** details are recorded of any item that necessarily changed and/or moved in the migration – i.e. was not just copied on a 1:1 basis.  
   - A **Migration Note** is attached to each item for which any field was moved or changed, with the reason and details of the old and new values, and the new location. 
   - An **Anomalies Report** with the same information is created and displayed in the UI language in a web browser as an **Anomalies Management Dashboard** for you to browse, search and filter, with the facility to check-off and filter out items that you have ‘fixed’ (by manual intervention in Jurism before migration or Zotero after), or choose to ignore. There is an option to run the migration with just a report and no RDF so you can check the Anomalies Report before you commit to the final run.   
   - **Migration Notes** and  **Anomalies Reports** are both in the user’s UI language. 

## Technical Design Principles     

1. **Non-invasive**. JZM treats the `jurism.sqlite` database (where your library is held) as *read-only*. The JZM outputs to an RDF file, which you import into Zotero using the standard Zotero procedure. Snapshots and Stored Files are faithfully copied into Zotero. Linked Attachments continue to be accessible by relative addressing in Zotero. 
2. **Table-driven**: the actual Jurism to Zotero mapping is meticulously mapped (every field of every item type) using a JSON input file. Database schemas, RDF generation, and UI language generation are similarly controlled by input JSON files, to facilitate maintenance.    
3. **Defensive programming** prevents crashes through a 'check before use’ rule on all inputs, and Error logging in AI readable form. Error logs can be sent to the developer (me) for fast diagnosis and correction. 

## Why migration is needed

This project grew from a personal need to move my 11,000 item library in Jurism 6 (with details of items in  several languages) away from Jurism. 


### Why leave Jurism? 

I have used **Zotero** since 2014, when I was writing my Masters dissertation. I moved to **Jurism** in 2020 because I needed the multilingual features, which Zotero did not have. It worked very well for my PhD thesis, which had a lot of German and other foreign language references that I need in both languages. 

I now have around 11,000 items in the database, 4,000 linked attachments and 1,000 stored attachments, snapshots etc.  

Sadly **Jurism** has not been maintained since 2021, and ... 

- Jurism is *not stable on Apple Silicon* and keeps crashing for no apparent reason. I recently upgraded to a Mac Mini M4 because Microsoft 365 stopped supporting older operating systems such as Catalina and Big Sur (Mac OS 10 and 11). 
- Zotero upgraded its authentication procedure for better security, and Jurism did not catch up, so Jurism has not been able to synchronise using the Zotero cloud for some time now. 
- There is no future plan to support or upgrade Jurism.

### Why go to Zotero?

I liked Zotero in the past, and I liked Jurism while it lasted. 

In the meantime, many of the language features that I need are available using the **Cite-Non-English** (CNE) plugin to Zotero. CNE is compatible with Zotero 10 and maintained up to date. 

I cross my fingers this will continue, but even if it doesn't, it now seems like a better long term strategy to stick with the official Zotero and use plugins (which Zotero vets) rather than relying on forks like Jurism (however good they are in the short term).  I might even develop my own plugin! 

### Why a migration program? 

Because the alternatives do not work! 

#### Failed: Export import 

If you Google how to migrate from Jurism to Zotero, the first answer is to export your library from Jurism to RDF, then import the RDF to a clean Zotero database. 

In my case however, Jurism gives highly cryptic error messages like 

- _[JavaScript Error: "XML Parsing Error: not well-formed_
- _[JavaScript Error: "Unsupported library type 'undefined' for library undefined" {file: "chrome://zotero/content/xpcom/uri.js" line: 112}]_ 

I have no idea how to fix these! I do not even know whether there is something corrupt in my database or a bug in Jurism. There is no obvious way of finding out. 

I found I could export individual items or small groups of items, but failed to find an algorithm that would export the whole library. 

In any case, this method does not include the multi-language variants that I need.  

#### Failed: Copy the whole database 

Google next suggests: copy the `jurism.sqlite` database from ``/Jurism` to `/Zotero`, rename it `zotero.sqlite`, and ask Zotero to read it. That does not work. 
Zotero recognises it as version 6.0.22m4 but thinks it is for a *future* Zotero release, even though that is way in the past!. 

I tried reverting to Zotero 6, but that and later versions did not recognise it wither. 

I tried manually editing the Version table using DB Browser for SQLite (to the last version 6 build, version 295) but was unable to trick Zotero into recognising it as an old Zotero database that just needed upgrading.

In any case, this method would also not include the multi-language information.

#### Rejected: Directly manipulate the Jurism database

It would be theoretically possible to 'clean up' the Jurism database to strip out the additional Jurism features, but ...  given the experience with just changing the version, it would be easy to corrupt the database and render it useless.      

#### Rejected: Insert directly into the new Zotero database

This is a variation that is theoretically possible, but would have the same danger of corrupting the database and render it useless, or worse, introduce a subtle corruption that appears good now but creates an unforeseen problem in the future. 

### Why this chosen solution? 

The chosen solution is non-invasive: 

1. Stage 1 is a custom program to extract the data from `jurism.sqlite` on a read-only basis - it does not change anything in `jurism.sqlite`. 
2. Stage 2 is to import this data using the built-in Zotero RDF import facility, so the integrity of your Zotero  database is secured. 

A bonus feature of having a custom program is that I could add enhancements beyond what the Jurism export would do (if it worked). 

## Installation and Use

*Provisional.* *Full details will be provided when Version 2.0 is ready.* 

- The **Jurism to Zotero Migration** package will be available as a ZIP file with the complete set of files required. 
- You download it to your disk and expand it in your user home directory as folder called **``/JZM`**.  
- The Extractor is a **Python3** script that can be run from your Terminal or using a click and play option. 
- It finds your **`jurism.sqlite`** database at the default location or one specified by you.
- It finds your **Jurism preferences** at the standard location for your host environment. 
- It writes the **Anomalies Management Dashboard**, the **RDF** file and the **`/files`** directory structure (Snapshots and Stored Files), ready for Zotero import, to  **``JZM/My Library`**. 







