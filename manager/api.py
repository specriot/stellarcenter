from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional, List

import db
import instance_manager
import settings

db.init()

app = FastAPI(title="LoyalCorp Instance Manager", version="1.0")


def _check_key(x_api_key: Optional[str] = Header(None)):
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class CreateRequest(BaseModel):
    bot_token:        str
    sip_user:         str
    sip_pass:         str
    name:             Optional[str] = None
    company_name:     Optional[str] = "LoyalCorp P1"
    support_username: Optional[str] = "@loyalcorpsupport"
    admin_ids:        Optional[List[int]] = []


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------

@app.post("/api/instances", status_code=201)
async def create_instance(req: CreateRequest, _=Depends(_check_key)):
    try:
        return await instance_manager.create_instance(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/instances")
def list_instances(_=Depends(_check_key)):
    return instance_manager.list_instances()


@app.get("/api/instances/{instance_id}")
def get_instance(instance_id: int, _=Depends(_check_key)):
    try:
        return instance_manager.get_instance(instance_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
