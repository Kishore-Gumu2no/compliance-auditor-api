"""Concepts learned to understand this are: Plain Text files and Binary Files.
    1. Plain text files: Files like .txt, .csv, .py are the examples of plain text files, THESE ARE LIKE POSTCARDS, 
       they are saved exactly as we type them, they don't have any custom fonts, layouts, etc, if we use standard 
       python's 'open()' command we get the readable words back.
       
    2. Binary files: Files like .pdf, .jpg, .exe are examples of binary files, THESE ARE LIKE LOCKED SAFES, they are 
       they are stored in complex binary code because these files not only store plain words or data, but also stuff
       like Layouts, Margins, Font, Spacing, Special Characters, Images, etc, therefore standard python's 'open()' 
       doesn't work, in order to open this LOCKED SAFE we need the RIGHT KEY, and PDFPLUMBER has the right key with it.
        
         
CONCEPT OF 'with': Whenever backend server interacts with computer's hard drive, it tells the operating system that 
                   it is using this particular file and TO NOT LET ANYONE DELETE THE FILE while it's reading, but what if
                   things go out of our hand and code crashes before the backend server even reads the file? THE FILE STAYS
                   LOCKED in server's memory forever, this is known as MEMORY LEAK, and if a thousand files get stuck 
                   simultaneously your backend server crashes, runs out of memory and dies, this is why we use 'with' command. """



import pdfplumber
def extract_pdf_text(file_path):
    with pdfplumber.open(file_path) as pdf:

        """When you use python's 'open()' this is like a room which takes the file opens, reads and expects the programmer's close()
        command. Nevertheless, we've got another special room called 'with open()', just because the keyword 'with' is present, 
        you enter a special room to read file, it takes the file reads, processes and closes on itself the second your code finishes
        reading your file!!! If we use this, we wouldn't have to worry about MEMEORY LEAK, because in case the code crashes 'with' 
        statement mathematically guarantees that it will close the file and unlock the file before the program dies."""
        
        first_page = pdf.pages[0].extract_text()
        return first_page


