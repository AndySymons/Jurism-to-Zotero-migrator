# Jurism-to-Zotero Migration 

A python program to extract data and attachments from a Jurism 6 environment and import them into a Zotero 9 environment. 

If you landed here, you probably already know that you want this program. If you are not sure, or want to know more about alternatives I tried first, why I needed this program, and why the migration is done this way (after several failed alternatives), then check out the Wiki page "Why?"

## Summary of features

Exports from a Jurism 6 database (on a read-only basis) to a standard Zotero RDF file and folder structure, as Jurism itself would (if it worked), if you select the options to export notes, attached files, and annotations. 

But it does more that that:  

1. Interprets Jurism language features and translates them directly into tags (in the Zotero 'Extra' field) that are recognised by the Zotero CNE plug-in. 

1. Cleans up language fields that are recognised, not standard ISO; e.g. 'spanish' to 'es'. 

1. Creates a report on links to missing files

1. Preserves the ‘Original date added' in the Extra field as “original-date-added:”. (Zotero changes the date added to that of the import dated, so the original date added is lost with a standard export). 

1. Avoids having to copy stored files by using symbolic links; Zotero import will copy the original files from Jurism/storage (using old Jurism hash codes) to its new Zotero/storage using Zotero's own new hash codes.

1. Makes all linked attachments in the Linked Attachments Base Directory available by relative addressing, even if they erroneously had absolute paths in Jurism. Avoids having to copy linked attachment files by simply transferring the Linked Attachments Base Directory over Zotero.  

## Prerequisites 

1.	Requires Python 3. It does not work on earlier versions. 
    
## Known limitations 

I made this for my own migration. It is freely shared, but I do have the resources to develop a program that works universally for all situations; it is only developed tested for the features that I actually use.  

1.	Operating system:  I only tested on MacOS 26 (Tahoe). It should work on other versions of Mac OS, and in Windows with minor changes to the procedure, but I cannot guarantee it or provide detailed instructions. 

1.	Jurism language settings: I only use European languages and the Latin alphabet, so settings for romanisation of other alphabets (such as Chinese) have not been tested. 

1.	Judicial fields: I never used any so have no way of testing them. Assume that they are not included in the present version. 

Feel free to fork the program if you want to tune it to your environment or add more features.   


# Operating instructions

For a detailed step-by-step guide see the Wiki. 


