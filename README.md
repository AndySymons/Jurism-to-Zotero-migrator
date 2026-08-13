# Jurism-to-Zotero Migration 

A python program to extract data and attachments from a Jurism 6 environment and import them into a Zotero 9 environment. 

If you landed here, you probably already know that you want this program. If you are not sure, or want to know more about alternatives I tried first, why I needed this program, and why the migration is done this way (after several failed alternatives), then check out the Wiki page "Why?"

## Summary of features

Exports from a Jurism 6 database (on a read-only basis) to a standard Zotero RDF file and folder structure, as Jurism itself would, if it were to work on a full library and if you were to select the Export options Notes, Attached files, and Annotations.  

But it does more that that:  
1. Interprets Jurism language features and translates them directly into tags (in the Zotero 'Extra' field) that are recognised by the Zotero and the CNE plug-in.  
1. Cleans up language fields that are recognised but not standard ISO; e.g. 'spanish' to 'es'.
1. Cleans up linked attachments that have legacy absolute paths in Jurism. Redefines them as relative to the Linked Attachments Base Directory.
1. Avoids having to copy linked attachment files by simply transferring the Linked Attachments Base Directory over Zotero.  
1. Copies stored files directly from Jurism/storage (using old Jurism hash codes) to its new Zotero/storage location using Zotero's own new hash codes.
1. Preserves the original 'Date added' and 'Modified' fields as tagged items in Zotero Extra field because Zotero changes these to  the date of the import.  
1. Creates a report with errors such as links to missing files and warnings such as changes to the language code 

## Prerequisites 

1.	Requires Python 3 to run the extractor. It does not work on earlier versions. 
1.	Zotero 9: it is not tested on earlier versions but might work. 
1.	Optionally uses the Zotero CNE plugin for translations of Titles and Containers. 
    
## Known limitations 

I made this for my own migration. It is freely shared, but I do have the resources to develop a program that works universally for all situations; it is only developed for the features that I actually use and tested on the environment that I have.   

1.	Operating system:  I only tested on MacOS 26 (Tahoe). It should work on other versions of Mac OS, and in Windows with minor changes to the procedure, but I cannot guarantee it or provide detailed instructions. 

1.	Jurism language settings: I only use European languages and the Latin alphabet, so use of and romanisation of other alphabets (such as Chinese) have not been tested. 

1.	Judicial fields: I never used any so have no way of testing them. They are not included in the present version. 

Feel free to fork the program if you want to tune it to your environment or add more features.   


# Operating instructions

For a detailed step-by-step guide see the Wiki page [Operating Instructions](https://github.com/AndySymons/Jurism-to-Zotero-migrator/wiki/5.-Operating-instructions). 
