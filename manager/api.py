from fastapi import FastAPI, HTTPException, Depends, Header, Query
from pydantic import BaseModel
from typing import Optional, List
import os

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


class UpdateRequest(BaseModel):
    bot_token:  Optional[str] = None
    admin_ids:  Optional[List[int]] = None
    name:       Optional[str] = None


# ---------------------------------------------------------------------------
# Instances — CRUD
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


@app.patch("/api/instances/{instance_id}")
def update_instance(instance_id: int, req: UpdateRequest, _=Depends(_check_key)):
    inst = db.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    try:
        return instance_manager.update_instance(instance_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/instances/{instance_id}", status_code=204)
async def delete_instance(instance_id: int, _=Depends(_check_key)):
    try:
        await instance_manager.delete_instance(instance_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Process control
# ---------------------------------------------------------------------------

@app.post("/api/instances/{instance_id}/start")
def start_instance(instance_id: int, _=Depends(_check_key)):
    inst = db.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    try:
        instance_manager.start_instance(instance_id)
        return {"status": "running"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/instances/{instance_id}/stop")
def stop_instance(instance_id: int, _=Depends(_check_key)):
    inst = db.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    try:
        instance_manager.stop_instance(instance_id)
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/instances/{instance_id}/pause")
def pause_instance(instance_id: int, _=Depends(_check_key)):
    inst = db.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    try:
        instance_manager.pause_instance(instance_id)
        return {"status": "paused"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/instances/{instance_id}/logs/{service}")
def get_logs(instance_id: int, service: str, lines: int = Query(default=100, le=2000), _=Depends(_check_key)):
    inst = db.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    if service not in ('bot', 'worker', 'asterisk'):
        raise HTTPException(status_code=400, detail="service must be bot, worker, or asterisk")
    inst_dir = inst['instance_dir']
    paths = {
        'bot':      os.path.join(inst_dir, 'logs', 'bot.log'),
        'worker':   os.path.join(inst_dir, 'logs', 'worker.log'),
        'asterisk': os.path.join(inst_dir, 'logs', 'messages'),
    }
    path = paths[service]
    if not os.path.exists(path):
        return {"instance_id": instance_id, "service": service, "lines": []}
    with open(path) as f:
        all_lines = f.readlines()
    tail = [l.rstrip('\n') for l in all_lines[-lines:]]
    return {"instance_id": instance_id, "service": service, "lines": tail}


@app.post("/api/instances/{instance_id}/resume")
def resume_instance(instance_id: int, _=Depends(_check_key)):
    inst = db.get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    try:
        instance_manager.resume_instance(instance_id)
        return {"status": "running"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
