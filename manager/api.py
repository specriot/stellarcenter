from fastapi import FastAPI, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel
from typing import Optional, List

import db
import instance_manager
import settings

db.init()

app = FastAPI(title="LoyalCorp Instance Manager", version="1.0")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check_key(key: Optional[str] = Security(_api_key_header)):
    if settings.API_KEY and key != settings.API_KEY:
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
async def create_instance(req: CreateRequest):
    _check_key()
    try:
        return await instance_manager.create_instance(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/instances")
def list_instances():
    _check_key()
    return instance_manager.list_instances()


@app.get("/api/instances/{instance_id}")
def get_instance(instance_id: int):
    _check_key()
    try:
        return instance_manager.get_instance(instance_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
