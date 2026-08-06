from PIL import Image
import pytesseract
import os

pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_PATH", r'C:\Program Files\Tesseract-OCR\tesseract.exe')
with Image.open("test_image.png") as image_obj:
    extracted_text = pytesseract.image_to_string(image_obj)
print(extracted_text) 