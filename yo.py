import requests

url = "http://127.0.0.1:8000/audit/"

# The exact format requests requires to send multiple files
files_to_send = [
    ('images', ('dummy.png', open('dummy.png', 'rb'), 'image/png')),
    ('pdfs', ('dummy.pdf', open('dummy.pdf', 'rb'), 'application/pdf'))
]

print("Firing request at server...")
response = requests.post(url, files=files_to_send)

print(f"Status: {response.status_code}")
print(response.json())