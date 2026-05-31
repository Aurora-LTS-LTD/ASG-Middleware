"""
ASG Solutions — FastAPI Backend
================================
This is the spine of the platform. It:
  1. Receives HTTP requests from the dashboard frontend
  2. Reads/writes data from the SQLite database via SQLAlchemy
  3. Exposes clean API endpoints at /api/v1/...
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
import datetime
import httpx   # for calling Make.com webhooks (async HTTP client)

from database import SessionLocal, engine, Business, Template, ActionLog, Category, TemplateBlueprint, Invoice, Base
from routers.whatsapp import router as whatsapp_router   # WhatsApp webhook handler
from services.tax_compliance import check_tax_compliance, calculate_vat, generate_invoice_number
from services.ita_api_service import request_allocation_number

# ─────────────────────────────────────────────────────────────
# CREATE TABLES ON STARTUP
# Ensures the .db file and tables exist before any request hits
# ─────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─────────────────────────────────────────────────────────────
# APP INSTANCE
# ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="ASG Solutions API",
    description="Backend for the ASG Solutions AI Automation Dashboard",
    version="1.0.0",
)

# ─────────────────────────────────────────────────────────────
# CORS MIDDLEWARE
# CORS = Cross-Origin Resource Sharing
# Without this, the browser blocks the frontend from calling the backend
# (because they run on the same machine but different "origins")
# allow_origins=["*"] → allow requests from any address (safe for local dev)
# ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────
# REGISTER ROUTERS
# "include_router" plugs a mini-server into the main app.
# Now any request to /webhook/whatsapp goes to whatsapp_router.
# ─────────────────────────────────────────────────────────────
app.include_router(whatsapp_router)

# ─────────────────────────────────────────────────────────────
# SERVE STATIC FILES (the dashboard HTML)
# This lets us open http://localhost:8000 and see the dashboard
# ─────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/dashboard", include_in_schema=False)
def serve_dashboard():
    return FileResponse("dashboard.html")


# ─────────────────────────────────────────────────────────────
# SERVE CLIENT PORTAL HTML
# Same pattern as /dashboard — serves the single-page portal app
# ─────────────────────────────────────────────────────────────
@app.get("/portal", include_in_schema=False)
def serve_portal():
    return FileResponse("client_portal.html")


# ─────────────────────────────────────────────────────────────
# DATABASE DEPENDENCY
# get_db() is called automatically by FastAPI for every endpoint
# that needs a database connection. It opens a session, gives it
# to the endpoint, and closes it when the request is done.
# Think of it like: open register → ring up customer → close register
# ─────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db          # "yield" = give the session to whoever asked for it
    finally:
        db.close()        # always close, even if an error happened


# ─────────────────────────────────────────────────────────────
# PYDANTIC SCHEMAS
# Pydantic = the data validator. It checks that incoming JSON has
# the right fields and types before we ever touch the database.
#
# *Create  = what the frontend SENDS us (input)
# *Response = what we SEND BACK to the frontend (output)
# ─────────────────────────────────────────────────────────────

class BusinessCreate(BaseModel):
    """Shape of the JSON body when creating a new business"""
    name:             str
    business_type:    str = "Other"
    channel:          str = "WhatsApp"
    status:           str = "active"
    telegram_api_key: Optional[str] = None
    category_id:      Optional[int] = None

class BusinessResponse(BaseModel):
    """Shape of the JSON we return when describing a business"""
    id:            int
    name:          str
    business_type: str
    channel:       str
    status:        str
    portal_token:  Optional[str] = None      # unique key for the client portal URL
    category_name: Optional[str] = None      # populated manually from the category relationship
    created_at:    datetime.datetime

    class Config:
        from_attributes = True   # allows converting SQLAlchemy objects → Pydantic


class TemplateCreate(BaseModel):
    """Shape of the JSON body when creating a new template"""
    name:             str
    description:      Optional[str] = None
    make_webhook_url: str
    business_id:      int

class TemplateResponse(BaseModel):
    """Shape of the JSON we return when describing a template"""
    id:               int
    name:             str
    description:      Optional[str]
    make_webhook_url: str
    business_id:      int
    business_name:    Optional[str] = None   # populated manually after query
    created_at:       datetime.datetime

    class Config:
        from_attributes = True


class CategoryCreate(BaseModel):
    """Shape of the JSON body when creating a new category"""
    name:        str
    description: Optional[str] = None

class CategoryResponse(BaseModel):
    """Shape of the JSON we return when describing a category"""
    id:              int
    name:            str
    description:     Optional[str]
    business_count:  Optional[int] = 0
    blueprint_count: Optional[int] = 0
    created_at:      datetime.datetime

    class Config:
        from_attributes = True


class BlueprintCreate(BaseModel):
    """Shape of the JSON body when creating a new blueprint"""
    name:             str
    description:      Optional[str] = None
    make_webhook_url: Optional[str] = None
    category_id:      int
    actions_config:   Optional[str] = None

class BlueprintResponse(BaseModel):
    """Shape of the JSON we return when describing a blueprint"""
    id:               int
    name:             str
    description:      Optional[str]
    make_webhook_url: Optional[str]
    category_id:      int
    category_name:    Optional[str] = None
    actions_config:   Optional[str]
    created_at:       datetime.datetime

    class Config:
        from_attributes = True


class InvoiceCreate(BaseModel):
    """Shape of the JSON body when creating a new invoice"""
    business_id:         int
    beneficiary_name:    str
    beneficiary_tax_id:  Optional[str] = None
    beneficiary_contact: Optional[str] = None
    amount_net:          float
    description:         Optional[str] = None

class InvoiceResponse(BaseModel):
    """Shape of the JSON we return when describing an invoice"""
    id:                  int
    business_id:         int
    invoice_number:      str
    beneficiary_name:    str
    beneficiary_tax_id:  Optional[str]
    beneficiary_contact: Optional[str]
    amount_net:          float
    vat_rate:            float
    vat_amount:          float
    amount_total:        float
    currency:            str
    requires_allocation: int
    allocation_number:   Optional[str]
    allocation_status:   str
    pdf_url:             Optional[str]
    status:              str
    description:         Optional[str]
    created_at:          datetime.datetime
    finalized_at:        Optional[datetime.datetime]
    business_name:       Optional[str] = None

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────────
# ROOT ENDPOINT
# A simple health check — proves the server is alive
# ─────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "message": "ASG Solutions API is running!",
        "version": "1.0.0",
        "dashboard": "http://localhost:8000/dashboard"
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 1: GET /api/v1/businesses
# Returns a list of ALL businesses in the database
#
# db: Session = Depends(get_db) → FastAPI automatically calls
# get_db() and passes the session here. We don't call it manually.
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/businesses")
def get_all_businesses(db: Session = Depends(get_db)):
    businesses = db.query(Business).all()
    result = []
    for b in businesses:
        data = BusinessResponse.model_validate(b).model_dump()
        # Attach the category name by looking up the relationship
        data["category_name"] = b.category.name if b.category else None
        data["portal_token"]  = b.portal_token
        result.append(data)
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 2: POST /api/v1/businesses
# Creates a new business record in the database
# The frontend sends JSON → Pydantic validates it → we save it
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/businesses", response_model=BusinessResponse)
def create_business(payload: BusinessCreate, db: Session = Depends(get_db)):
    # Create a new Business object from the validated payload
    new_business = Business(
        name             = payload.name,
        business_type    = payload.business_type,
        channel          = payload.channel,
        status           = payload.status,
        telegram_api_key = payload.telegram_api_key,
        category_id      = payload.category_id,
    )
    db.add(new_business)      # add to the session (queued, not saved yet)
    db.commit()               # write to the database file
    db.refresh(new_business)  # reload from db to get the auto-generated id
    return new_business


# ─────────────────────────────────────────────────────────────
# ENDPOINT 3: DELETE /api/v1/businesses/{business_id}
# Deletes a business by its ID
# {business_id} is a path parameter — part of the URL itself
# ─────────────────────────────────────────────────────────────
@app.delete("/api/v1/businesses/{business_id}")
def delete_business(business_id: int, db: Session = Depends(get_db)):
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        # 404 = "Not Found" — standard HTTP error code
        raise HTTPException(status_code=404, detail="Business not found")
    db.delete(business)
    db.commit()
    return {"message": f"Business '{business.name}' deleted successfully"}


# ─────────────────────────────────────────────────────────────
# ENDPOINT 4: PUT /api/v1/businesses/{business_id}
# Updates the name (or other fields) of an existing business
# ─────────────────────────────────────────────────────────────
class BusinessUpdate(BaseModel):
    name:          Optional[str] = None
    business_type: Optional[str] = None
    channel:       Optional[str] = None
    status:        Optional[str] = None

@app.put("/api/v1/businesses/{business_id}", response_model=BusinessResponse)
def update_business(business_id: int, payload: BusinessUpdate, db: Session = Depends(get_db)):
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    # Only update fields that were actually sent (not None)
    if payload.name is not None:          business.name          = payload.name
    if payload.business_type is not None: business.business_type = payload.business_type
    if payload.channel is not None:       business.channel       = payload.channel
    if payload.status is not None:        business.status        = payload.status
    db.commit()
    db.refresh(business)
    return business


# ─────────────────────────────────────────────────────────────
# ENDPOINT 5a: GET /api/v1/templates
# Returns ALL templates across all businesses, with business name
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/templates")
def get_all_templates(db: Session = Depends(get_db)):
    templates = db.query(Template).all()
    result = []
    for t in templates:
        data = TemplateResponse.model_validate(t).model_dump()
        # Attach the business name by looking up the owner relationship
        data["business_name"] = t.owner.name if t.owner else "Unknown"
        result.append(data)
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 5b: GET /api/v1/businesses/{business_id}/templates
# Returns all templates that belong to a specific business
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/businesses/{business_id}/templates", response_model=list[TemplateResponse])
def get_templates_for_business(business_id: int, db: Session = Depends(get_db)):
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    return business.templates


# ─────────────────────────────────────────────────────────────
# ENDPOINT 6: POST /api/v1/templates
# Creates a new automation template and links it to a business
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/templates", response_model=TemplateResponse)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)):
    # Verify the parent business exists
    business = db.query(Business).filter(Business.id == payload.business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    new_template = Template(
        name             = payload.name,
        description      = payload.description,
        make_webhook_url = payload.make_webhook_url,
        business_id      = payload.business_id,
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return new_template


# ─────────────────────────────────────────────────────────────
# ENDPOINT 6b: DELETE /api/v1/templates/{template_id}
# Removes a template from the database
# ─────────────────────────────────────────────────────────────
@app.delete("/api/v1/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(Template).filter(Template.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    name = template.name
    db.delete(template)
    db.commit()
    return {"message": f"Template '{name}' deleted successfully"}


# ─────────────────────────────────────────────────────────────
# ENDPOINT 7: POST /api/v1/templates/{template_id}/trigger
# Fires the Make.com webhook for a specific template
# httpx.AsyncClient → makes an async HTTP call to Make.com
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/templates/{template_id}/trigger")
async def trigger_template(template_id: int, db: Session = Depends(get_db)):
    # ── Step 1: Find the template in the database ──
    template = db.query(Template).filter(Template.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # ── Step 2: Get the business name from the relationship ──
    business_name = template.owner.name if template.owner else "Unknown"
    timestamp     = datetime.datetime.utcnow().isoformat() + "Z"

    # ── Step 3: Build the payload we send to Make.com ──
    webhook_payload = {
        "template_id":   template.id,
        "template_name": template.name,
        "business_name": business_name,
        "business_id":   template.business_id,
        "triggered_at":  timestamp,
    }

    # ── Step 4: Check if a real webhook URL exists ──
    url = (template.make_webhook_url or "").strip()

    if not url or not url.startswith("https://"):
        # No valid webhook — return simulated response (safe for testing)
        print(f"[TRIGGER-SIM] '{template.name}' for '{business_name}' at {timestamp}")

        # ── LOG: record the simulated trigger in the journal ──
        log = ActionLog(
            template_id  = template.id,
            business_id  = template.business_id,
            status       = "simulated",
            detail       = "No valid webhook URL — trigger simulated",
        )
        db.add(log)
        db.commit()

        return {
            "status":   "simulated",
            "template": template.name,
            "business": business_name,
            "message":  f"No valid webhook URL — trigger simulated for {template.name}",
        }

    # ── Step 5: Send the real HTTP POST to Make.com ──
    print(f"[TRIGGER-LIVE] Calling {url} for '{template.name}' / '{business_name}'")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=webhook_payload,
                timeout=15.0,
            )

        # Make.com returns 200 on success — anything else is a problem
        if response.status_code >= 400:
            # ── LOG: record the failure ──
            log = ActionLog(
                template_id  = template.id,
                business_id  = template.business_id,
                status       = "failed",
                detail       = f"Make.com returned error {response.status_code}",
            )
            db.add(log)
            db.commit()

            raise HTTPException(
                status_code=502,
                detail=f"Make.com returned error {response.status_code}: {response.text[:200]}",
            )

        # ── LOG: record the successful trigger ──
        log = ActionLog(
            template_id  = template.id,
            business_id  = template.business_id,
            status       = "triggered",
            detail       = f"Make.com responded with {response.status_code}",
        )
        db.add(log)
        db.commit()

        return {
            "status":      "triggered",
            "template":    template.name,
            "business":    business_name,
            "make_status": response.status_code,
            "message":     f"Triggered {template.name} for {business_name}",
        }

    except httpx.TimeoutException:
        # ── LOG: record timeout failure ──
        log = ActionLog(
            template_id  = template.id,
            business_id  = template.business_id,
            status       = "failed",
            detail       = "Make.com did not respond within 15 seconds",
        )
        db.add(log)
        db.commit()

        raise HTTPException(
            status_code=504,
            detail=f"Make.com did not respond within 15 seconds",
        )
    except httpx.ConnectError:
        # ── LOG: record connection failure ──
        log = ActionLog(
            template_id  = template.id,
            business_id  = template.business_id,
            status       = "failed",
            detail       = "Could not connect to Make.com",
        )
        db.add(log)
        db.commit()

        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to Make.com — check the webhook URL",
        )
    except HTTPException:
        raise   # re-raise our own exceptions untouched
    except Exception as e:
        # ── LOG: record unexpected failure ──
        log = ActionLog(
            template_id  = template.id,
            business_id  = template.business_id,
            status       = "failed",
            detail       = f"Unexpected error: {str(e)}",
        )
        db.add(log)
        db.commit()

        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error calling webhook: {str(e)}",
        )


# ─────────────────────────────────────────────────────────────
# ENDPOINT 8: GET /api/v1/logs
# Returns the action log — every trigger ever fired.
# Think of it as the doctor's journal: who, what, when, result.
# Sorted newest-first so the latest activity shows on top.
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/logs")
def get_logs(db: Session = Depends(get_db)):
    logs = db.query(ActionLog).order_by(ActionLog.triggered_at.desc()).all()
    result = []
    for log in logs:
        # Look up the template and business names for display
        template_name = log.template.name if log.template else "Deleted Template"
        business_name = log.business.name if log.business else "Deleted Business"
        result.append({
            "id":            log.id,
            "template_id":   log.template_id,
            "template_name": template_name,
            "business_id":   log.business_id,
            "business_name": business_name,
            "status":        log.status,
            "detail":        log.detail,
            "triggered_at":  log.triggered_at.isoformat() if log.triggered_at else None,
        })
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 10: GET /api/v1/categories
# Returns all categories with the count of businesses and
# blueprints that belong to each one
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/categories")
def get_all_categories(db: Session = Depends(get_db)):
    categories = db.query(Category).all()
    result = []
    for cat in categories:
        data = CategoryResponse.model_validate(cat).model_dump()
        # Manually count related businesses and blueprints
        data["business_count"]  = len(cat.businesses)
        data["blueprint_count"] = len(cat.blueprints)
        result.append(data)
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 11: POST /api/v1/categories
# Creates a new category (e.g. "Restaurant", "Garage", etc.)
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/categories", response_model=CategoryResponse)
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    new_category = Category(
        name        = payload.name,
        description = payload.description,
    )
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    return new_category


# ─────────────────────────────────────────────────────────────
# ENDPOINT 12: DELETE /api/v1/categories/{category_id}
# Deletes a category by its ID
# ─────────────────────────────────────────────────────────────
@app.delete("/api/v1/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    name = category.name
    db.delete(category)
    db.commit()
    return {"message": f"Category '{name}' deleted successfully"}


# ─────────────────────────────────────────────────────────────
# ENDPOINT 13: GET /api/v1/categories/{category_id}/blueprints
# Returns all blueprints that belong to a specific category
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/categories/{category_id}/blueprints")
def get_blueprints_for_category(category_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    result = []
    for bp in category.blueprints:
        data = BlueprintResponse.model_validate(bp).model_dump()
        data["category_name"] = category.name
        result.append(data)
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 14: POST /api/v1/blueprints
# Creates a new blueprint and links it to a category
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/blueprints", response_model=BlueprintResponse)
def create_blueprint(payload: BlueprintCreate, db: Session = Depends(get_db)):
    # Verify the parent category exists
    category = db.query(Category).filter(Category.id == payload.category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    new_blueprint = TemplateBlueprint(
        name             = payload.name,
        description      = payload.description,
        make_webhook_url = payload.make_webhook_url,
        category_id      = payload.category_id,
        actions_config   = payload.actions_config,
    )
    db.add(new_blueprint)
    db.commit()
    db.refresh(new_blueprint)
    return new_blueprint


# ─────────────────────────────────────────────────────────────
# ENDPOINT 15: DELETE /api/v1/blueprints/{blueprint_id}
# Removes a blueprint from the database
# ─────────────────────────────────────────────────────────────
@app.delete("/api/v1/blueprints/{blueprint_id}")
def delete_blueprint(blueprint_id: int, db: Session = Depends(get_db)):
    blueprint = db.query(TemplateBlueprint).filter(TemplateBlueprint.id == blueprint_id).first()
    if not blueprint:
        raise HTTPException(status_code=404, detail="Blueprint not found")
    name = blueprint.name
    db.delete(blueprint)
    db.commit()
    return {"message": f"Blueprint '{name}' deleted successfully"}


# ─────────────────────────────────────────────────────────────
# ENDPOINT 16: GET /api/v1/portal/{token}
# CLIENT PORTAL — the main endpoint for the client-facing portal.
# Each business has a unique portal_token (like a "key card").
# The client visits /portal?token=XXXX, the frontend JS calls
# this API to load everything the client needs to see:
#   - their business info
#   - their category and available blueprints
#   - trigger stats and recent activity logs
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/portal/{token}")
def get_portal_data(token: str, db: Session = Depends(get_db)):
    # ── Step 1: Look up the business by its portal_token ──
    business = db.query(Business).filter(Business.portal_token == token).first()
    if not business:
        raise HTTPException(status_code=404, detail="Invalid portal token — business not found")

    # ── Step 2: Build the business info block ──
    category = business.category  # may be None if no category assigned
    business_info = {
        "id":            business.id,
        "name":          business.name,
        "channel":       business.channel,
        "status":        business.status,
        "category_name": category.name if category else None,
        "created_at":    business.created_at.isoformat() if business.created_at else None,
    }

    # ── Step 3: Build the category info block ──
    category_info = None
    if category:
        category_info = {
            "name":        category.name,
            "description": category.description,
        }

    # ── Step 4: Fetch blueprints for this business's category ──
    blueprints = []
    if category:
        bps = db.query(TemplateBlueprint).filter(
            TemplateBlueprint.category_id == category.id
        ).all()
        for bp in bps:
            blueprints.append({
                "id":               bp.id,
                "name":             bp.name,
                "description":      bp.description,
                "make_webhook_url": bp.make_webhook_url,
                "actions_config":   bp.actions_config,
                "created_at":       bp.created_at.isoformat() if bp.created_at else None,
            })

    # ── Step 5: Calculate trigger stats from ActionLog ──
    total_triggers = db.query(ActionLog).filter(
        ActionLog.business_id == business.id
    ).count()

    today = datetime.date.today()
    triggers_today = db.query(ActionLog).filter(
        ActionLog.business_id == business.id,
        ActionLog.triggered_at >= datetime.datetime.combine(today, datetime.time.min),
        ActionLog.triggered_at <  datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min),
    ).count()

    successful_triggers = db.query(ActionLog).filter(
        ActionLog.business_id == business.id,
        ActionLog.status == "triggered",
    ).count()

    failed_triggers = db.query(ActionLog).filter(
        ActionLog.business_id == business.id,
        ActionLog.status == "failed",
    ).count()

    stats = {
        "total_triggers":      total_triggers,
        "triggers_today":      triggers_today,
        "successful_triggers": successful_triggers,
        "failed_triggers":     failed_triggers,
    }

    # ── Step 6: Fetch the last 20 log entries for this business ──
    recent_logs_raw = db.query(ActionLog).filter(
        ActionLog.business_id == business.id
    ).order_by(ActionLog.triggered_at.desc()).limit(20).all()

    recent_logs = []
    for log in recent_logs_raw:
        template_name = log.template.name if log.template else "Deleted Template"
        recent_logs.append({
            "id":            log.id,
            "status":        log.status,
            "detail":        log.detail,
            "template_name": template_name,
            "triggered_at":  log.triggered_at.isoformat() if log.triggered_at else None,
        })

    # ── Step 7: Return the full portal payload ──
    return {
        "business":   business_info,
        "category":   category_info,
        "blueprints": blueprints,
        "stats":      stats,
        "recent_logs": recent_logs,
    }


# ─────────────────────────────────────────────────────────────
# ENDPOINT 18: GET /api/v1/invoices
# Returns ALL invoices across all businesses, with business_name
# populated from the relationship. Like viewing the full receipt book.
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/invoices")
def get_all_invoices(db: Session = Depends(get_db)):
    invoices = db.query(Invoice).all()
    result = []
    for inv in invoices:
        data = InvoiceResponse.model_validate(inv).model_dump()
        # Attach the business name by looking up the relationship
        data["business_name"] = inv.business.name if inv.business else "Unknown"
        result.append(data)
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 19: GET /api/v1/businesses/{business_id}/invoices
# Returns all invoices that belong to a specific business
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/businesses/{business_id}/invoices")
def get_invoices_for_business(business_id: int, db: Session = Depends(get_db)):
    business = db.query(Business).filter(Business.id == business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    invoices = db.query(Invoice).filter(Invoice.business_id == business_id).all()
    result = []
    for inv in invoices:
        data = InvoiceResponse.model_validate(inv).model_dump()
        data["business_name"] = business.name
        result.append(data)
    return result


# ─────────────────────────────────────────────────────────────
# ENDPOINT 20: POST /api/v1/invoices
# Creates a new invoice for a business.
#
# Steps:
#   1. Verify the business exists
#   2. Calculate VAT (17%) using the tax compliance service
#   3. Check if allocation is required (based on total amount)
#   4. Generate a unique invoice number (e.g. "INV-001")
#   5. Save the invoice record as a "draft"
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/invoices", response_model=InvoiceResponse)
def create_invoice(payload: InvoiceCreate, db: Session = Depends(get_db)):
    # ── Step 1: Verify the parent business exists ──
    business = db.query(Business).filter(Business.id == payload.business_id).first()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")

    # ── Step 2: Calculate VAT using the tax compliance service ──
    vat_info = calculate_vat(payload.amount_net)
    # vat_info contains: vat_rate, vat_amount, amount_total

    # ── Step 3: Check if allocation is required (Israeli tax authority rules) ──
    compliance = check_tax_compliance(payload.amount_net)
    # compliance contains: requires_allocation (bool), threshold, etc.

    # ── Step 4: Generate a unique invoice number for this business ──
    existing_count = db.query(Invoice).filter(
        Invoice.business_id == payload.business_id
    ).count()
    invoice_number = generate_invoice_number(payload.business_id, existing_count)

    # ── Step 5: Create the Invoice record with all calculated fields ──
    new_invoice = Invoice(
        business_id         = payload.business_id,
        invoice_number      = invoice_number,
        beneficiary_name    = payload.beneficiary_name,
        beneficiary_tax_id  = payload.beneficiary_tax_id,
        beneficiary_contact = payload.beneficiary_contact,
        amount_net          = payload.amount_net,
        vat_rate            = vat_info["vat_rate"],
        vat_amount          = vat_info["vat_amount"],
        amount_total        = vat_info["amount_total"],
        currency            = "ILS",
        requires_allocation = 1 if compliance["requires_allocation"] else 0,
        allocation_status   = "pending" if compliance["requires_allocation"] else "not_required",
        description         = payload.description,
        status              = "draft",
    )
    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)
    return new_invoice


# ─────────────────────────────────────────────────────────────
# ENDPOINT 21: POST /api/v1/invoices/{invoice_id}/finalize
# Finalizes a draft invoice — locks it and (if needed) requests
# an allocation number from the Israeli Tax Authority (ITA).
#
# Steps:
#   1. Find the invoice and verify it's still a "draft"
#   2. If allocation is required → call the ITA API
#   3. On success → save allocation number, mark "approved"
#   4. On failure → mark "failed", return error
#   5. Set status to "finalized" and record the timestamp
# ─────────────────────────────────────────────────────────────
@app.post("/api/v1/invoices/{invoice_id}/finalize", response_model=InvoiceResponse)
async def finalize_invoice(invoice_id: int, db: Session = Depends(get_db)):
    # ── Step 1: Find the invoice and check its status ──
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "draft":
        raise HTTPException(status_code=400, detail=f"Invoice is already '{invoice.status}' — only draft invoices can be finalized")

    # ── Step 2: If allocation is required, call the ITA API ──
    if invoice.requires_allocation == 1 and invoice.allocation_status == "pending":
        ita_response = await request_allocation_number()

        if ita_response.get("success"):
            # ── Step 3a: Allocation approved — save the number ──
            invoice.allocation_number = ita_response["allocation_number"]
            invoice.allocation_status = "approved"
        else:
            # ── Step 3b: Allocation failed — mark as failed and return error ──
            invoice.allocation_status = "failed"
            db.commit()
            db.refresh(invoice)
            raise HTTPException(
                status_code=502,
                detail=f"ITA allocation request failed: {ita_response.get('error', 'Unknown error')}",
            )

    # ── Step 4: Finalize the invoice ──
    invoice.status       = "finalized"
    invoice.finalized_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(invoice)
    return invoice


# ─────────────────────────────────────────────────────────────
# ENDPOINT 22: GET /api/v1/invoices/{invoice_id}
# Returns a single invoice by its ID, with business_name populated
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/invoices/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    data = InvoiceResponse.model_validate(invoice).model_dump()
    data["business_name"] = invoice.business.name if invoice.business else "Unknown"
    return data


# ─────────────────────────────────────────────────────────────
# ENDPOINT 17: GET /api/v1/overview
# Returns summary numbers for the Overview dashboard page
# ─────────────────────────────────────────────────────────────
@app.get("/api/v1/overview")
def get_overview(db: Session = Depends(get_db)):
    total_businesses  = db.query(Business).count()
    active_businesses = db.query(Business).filter(Business.status == "active").count()
    total_templates   = db.query(Template).count()
    total_categories  = db.query(Category).count()
    total_invoices    = db.query(Invoice).count()
    today = datetime.date.today()
    triggers_today = db.query(ActionLog).filter(
        ActionLog.triggered_at >= datetime.datetime.combine(today, datetime.time.min),
        ActionLog.triggered_at <  datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time.min),
    ).count()
    triggers_total = db.query(ActionLog).count()
    return {
        "total_businesses":  total_businesses,
        "active_businesses": active_businesses,
        "total_templates":   total_templates,
        "total_categories":  total_categories,
        "total_invoices":    total_invoices,
        "triggers_today":    triggers_today,
        "triggers_total":    triggers_total,
        "system_status":     "online",
    }
