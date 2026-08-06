"""In this file, the main topic is not the asynchronous programming part, it is about '@', this is a decorator.

WHAT IT DOES: It signifies that the following function after using '@' is a special kind of function called DECORATOR function.

WHY USE THIS: Usually when you write a function, let's say Audit, if you gotta use that function you need to call it manually by typing
              result = Audit("Hi Hello I am Kishore"), this is okay but not productive when you don't know when we will have to call it,
              so we need a SPECIAL INSTRUCTOR MARKER (@) to mark the function SPECIAL and tell the compiler that this function is now called
              by ANOTHER PIECE OF CODE (functions) and not the programmer.
              
HOW DOES IT WORK: Now that th programmer has no right to call the function independently, then how will this function be called? It will
                  called by another PIECE OF CODE (functions). Example: In this file we have got ' @server.post("/audit/") ', what this 
                  means is:
                     1. The function below this line, Audit, is marked as a special function, saying that it cannot be called by the 
                        programmer anymore in this file.
                     2. Audit function's control of calling is handed over to server, i.e an instant of the FastAPI program, which means,
                        THE CONTROL IS BEING HANDED OVER TO ANOTHER PIECE OF CODE.
                     3. The ' .post("/audit/") ' here is the trigger, if any user visits a website such as https://www.linkedin.com and 
                        clicks the "AUDIT" button, it redirects to https://www.linkedin.com/audit/ and this is when our async Audit function
                        gets triggered or called."""




from fastapi import FastAPI, HTTPException, WebSocket, UploadFile, File, WebSocketDisconnect
from pydantic import BaseModel, ValidationError
from orchestrator import app, run_audit, supabase_db
import json
import asyncio
import hashlib
from document_parser import extract_pdf_text
from typing import List, Annotated

async def run_delivery_streams():
    """ About async functions: The main thing we neeed to keep in mind is that async functions are not called by the programmer, they are called by the computer 
    when it is ready to call them. The main reason for this is that async functions are not executed in the order they are called they are executed in the order of 
    their completion, which means that if an async function takes longer to complete than another async function, the second async function will be executed first.
    This is the main reason why async functions are not called by the programmer, because the programmer cannot predict when an async function will complete,
    and that's why it is risky to get called by the programmer, rather we programmers just write the code for async functions and let the system decide when to call them.
    AND INITIALIZING A FUNCTION WITH 'async' KEYWORD DOES NOT MAKE IT AN ASYNC FUNCTION, even "await" keyword must be used inside the function to make it an async function
    otherwise it will be a normal function.  """
import asyncio
import websockets
import json
async def Zomato():
        Zomato_earnings = 4000
        while True:
            try:
                async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
                    payload = {"platform": "Zomato", "earnings": Zomato_earnings}
                    await ws.send(json.dumps(payload))
                    response_Zomato = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
                    print(f"Zomato received: {response_Zomato}")
                    Zomato_earnings += 100
                    await asyncio.sleep(5)
            except Exception as e:
                print(f"Zomato connection failed, retrying in 5s... Error: {e}")
                await asyncio.sleep(5)

async def Swiggy():
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Swiggy_earnings = 3500
        while True:
            try:
                payload = {"platform": "Swiggy", "earnings": Swiggy_earnings}
                await ws.send(json.dumps(payload))
                response_Swiggy = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
                print(f"Swiggy received: {response_Swiggy}")
                Swiggy_earnings += 100
                await asyncio.sleep(13)
            except Exception as e:
                print(f"Swiggy connection failed, retrying in 5s... Error: {e}")
                await asyncio.sleep(5)

async def UberEats():
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Uber_earnings = 1700
        while True:
            try:
                payload = {"platform": "UberEats", "earnings": Uber_earnings}
                await ws.send(json.dumps(payload))
                response_Uber = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
                print(f"Uber received: {response_Uber}")
                Uber_earnings += 100
                await asyncio.sleep(18)
            except Exception as e:
                print(f"UberEats connection failed, retrying in 5s... Error: {e}")
                await asyncio.sleep(5)

async def main():
            #region asyncio.gather explanation
            """This 'asyncio.gather' is real important because this function is the reason why all the three async functions are sent to the event loop at the same time, and
            the why they are running in parallel, if we didn't use this function these three async functions would have been executed one after the other or sequentially,  which
            is not what we want.
            When we put 'await' infront of 'asyncio.gather' this happens:
            1. Send three async functions to the event loop at the same time.
            2. Wait for all three async functions to complete and stops reading the code below this line until all three async functions are completed.
            3. Once all three async functions are completed, it continues reading the code below this line."""
            #endregion
            await asyncio.sleep(2)
            await asyncio.gather(
                Zomato(),
                Swiggy(),
                UberEats()
            )

#region Important note about asyncio.run(main())
"""This line is important because it tells the client to run the main function, which is the entry point of the program, and it is also important because it tells
 the client to run the main function in an event loop, which is a loop that runs forever and keeps the program running until it is stopped by the user. This is like the
 master ignition switch of the program, it provisions the event loop in the operating system and runs until the program is stopped by the user."""
#endregion

async def lifespan(app: FastAPI):
    """This is a special function called lifespan, it is a special function because it is called by the FastAPI framework when the server starts and stops, and it is used to
    perform any setup or teardown tasks that need to be done when the server starts or stops. This is important because we want to start the async functions when the server starts
    and stop them when the server stops, so we use this special function to do that."""
    task = asyncio.create_task(run_delivery_streams())
    yield


server = FastAPI(lifespan=lifespan)
""" #This line is important because it tells the FastAPI framework to use the lifespan function when the server starts and stops, and it is used to perform any setup or teardown tasks that 
need to be done when the server starts or stops. This is important because we want to start the async functions when the server starts and stop them when the server stops, so we use this
special function to do that."""

class StreamData(BaseModel):
    platform: str
    earnings: int

@server.post("/audit/")
async def Audit(
    images: Annotated[list[UploadFile] | None, File()] = None,
    pdfs: Annotated[list[UploadFile] | None, File()] = None
    ):
    #region UploadFile Explanation

    """'Upload_file' acts exactly like a constructor, basically it creates an object by taking the raw, messy, stream of binary data arriving
   at server and tranforms into a clean python object so that we can use methods like .filename or .read(). It also tells the computer to store 
   smaller files to store in RAM, but if the file is huge then it instructs computer to store files in hard drive.

   'File()' acts as a locator, when a HTTP request is made by the user, the request received by server consists of various sections such as:
   URL, headers, JSON body and the multipart/form-data. What 'File()' does is, it instructs FastAPI to not search through everything, rather it 
   instructs FastAPI to ignore everything and directly go to multipart/form-data section of the HTTP request, grab data there and use THIS PARTICULAR
   DATA TO BUILD THE 'UploadFile' OBJECT"""
   
   #endregion
    if images is None:
        images = []
    if pdfs is None:
        pdfs = []
    if len(images) > 20:
        raise HTTPException(status_code= 400, detail = "Too many images !!")
    if len(pdfs) > 5:
        raise HTTPException(status_code= 400, detail = "Too many PDFs !!")
    saved_pdf_paths = set()
    saved_image_paths = set()
    for pdf in pdfs:
        file_bytes = await pdf.read()
        hasher_pdf = hashlib.sha256()
        hasher_pdf.update(file_bytes)
        file_hash = hasher_pdf.hexdigest()
        with open(f"temp_{file_hash}.pdf", "wb") as pdf_obj:
            pdf_obj.write(file_bytes)
        saved_pdf_paths.add(f"temp_{file_hash}.pdf")

    for image in images:
        image_bytes = await image.read()
        hasher_img = hashlib.sha256()
        hasher_img.update(image_bytes)
        img_hash = hasher_img.hexdigest()
        with open(f"temp_{img_hash}.png", "wb") as image_obj:
            image_obj.write(image_bytes)
        saved_image_paths.add(f"temp_{img_hash}.png")

    ai_result = run_audit(saved_pdf_paths, saved_image_paths)
    
    return{
        "status": "Audit Complete",
        "ai_analysis": ai_result
    }
                        
   

    #region Pydantic clarity 

    """Pydantic is a package and BaseModel is the rulebook template we want our data to follow, so here when the worker_message 
       comes in as initial input, the BaseModel class' inherited AuditRequest class checks if the data is perfectly formatted, if 
       yes it builds an object and in order to save the object we need a variable that is what "request" is doing here."""

    #endregion


@server.websocket("/stream")
async def ingest_stream(websocket: WebSocket):
    await websocket.accept() #This line is important because it tells the server to accept the connection, if we don't have this line then the server will not accept the connection 
    while True:
        #region Infinite while loop

        """The reason why we haven't put 'while True' above websocket.accept() is because if we did then, the 
         server will try to open the connection on 1st message, then again try to open the SAME CONNECTION again
         on the second message because it is a while LOOP, it will iterate the process of accepting the connection,
         which would make the server crash. """
        
        #endregion
        try:
            data = await websocket.receive_json() #This line is important because it tells the server(or websocket in this instance) to receive the data in JSON format
            secure_data = StreamData(**data) #This line is important because it tells the server to validate the data received from websocket, if the data is not valid then it will throw an error
            print(f"STREAM RECEIVE: Logged {secure_data.earnings} from {secure_data.platform}")
            try:
                supabase_db.table("earnings").insert({
                    "platform": secure_data.platform,
                    "amount": secure_data.earnings
                }).execute()
            except Exception as e:
                print(f"Database error: {e}")
            await websocket.send_text(f"Logged earnings of {secure_data.earnings} from {secure_data.platform}")
        except WebSocketDisconnect:
            break 
            #region No use of return statement in websockets

            """Here in web sockets we don't use return in except block, if we do then return terminates the, and
            we of course do not want the websocket to close, that's why we use websocket.send_text. """

            #endregion
        except ValidationError as e:
            await websocket.send_text(f"Invalid Data Format received")
            continue
            



    

