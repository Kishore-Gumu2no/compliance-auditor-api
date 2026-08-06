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
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Zomato_earnings = 4000
        while True:
            payload = {"platform": "Zomato", "earnings": Zomato_earnings}
            await ws.send(json.dumps(payload))
            response_Zomato = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
            print(f"Zomato received: {response_Zomato}")
            Zomato_earnings += 100
            await asyncio.sleep(5)

async def Swiggy():
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Swiggy_earnings = 3500
        while True:
            payload = {"platform": "Swiggy", "earnings": Swiggy_earnings}
            await ws.send(json.dumps(payload))
            response_Swiggy = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
            print(f"Swiggy received: {response_Swiggy}")
            Swiggy_earnings += 100
            await asyncio.sleep(13)

async def UberEats():
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Uber_earnings = 1700
        while True:
            payload = {"platform": "UberEats", "earnings": Uber_earnings}
            await ws.send(json.dumps(payload))
            response_Uber = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
            print(f"Uber received: {response_Uber}")
            Uber_earnings += 100
            await asyncio.sleep(18)

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
            await asyncio.gather(
                Zomato(),
                Swiggy(),
                UberEats()
            )

asyncio.run(main()) 
#region Important note about asyncio.run(main())
"""This line is important because it tells the client to run the main function, which is the entry point of the program, and it is also important because it tells
 the client to run the main function in an event loop, which is a loop that runs forever and keeps the program running until it is stopped by the user. This is like the
 master ignition switch of the program, it provisions the event loop in the operating system and runs until the program is stopped by the user."""
#endregion