"""
DentAI Pro — FastAPI REST layer
Wraps the LangGraph agent with proper HTTP endpoints,
JWT authentication, and multi-tenant clinic isolation.

Endpoints:
  POST /auth/token          — get JWT token
  POST /chat                — send message to agent
  GET  /availability        — get available slots
  GET  /appointments/{id}   — get patient appointments
  POST /appointments/book   — book appointment
  POST /appointments/cancel — cancel appointment

Run locally:
  uvicorn api.main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
import os
from dotenv import load_dotenv

# ── JWT imports ──────────────────────────────────────────────
from jose import JWTError, jwt
from passlib.context import CryptContext

# ── Agent import ─────────────────────────────────────────────
from scripts.dental_agent_groq import run_dental_agent

load_dotenv()

# ── Config ───────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 30))

# ── Simple in-memory clinic registry (replace with DB in prod) ──
# Each clinic has an ID, name, and hashed password
CLINICS = {
    "demo_clinic": {
        "clinic_name": "Demo Dental Clinic",
        "hashed_password": "$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",  # "secret"
        "db_schema": "dentai_pro"  # extend for multi-tenancy
    }
}

# ── Auth setup ───────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

app = FastAPI(
    title="DentAI Pro API",
    description="AI-powered dental appointment management system",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict to your Streamlit URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ──────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str
    intent_detected: Optional[str] = None

class AvailabilityRequest(BaseModel):
    specialization: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD
    doctor_name: Optional[str] = None

# ── Auth helpers ─────────────────────────────────────────────
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_clinic(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        clinic_id: str = payload.get("sub")
        if clinic_id is None or clinic_id not in CLINICS:
            raise credentials_exception
        return CLINICS[clinic_id]
    except JWTError:
        raise credentials_exception

# ── Routes ───────────────────────────────────────────────────
@app.get("/health")
def health_check():
    """Health check endpoint — used by Render to verify the service is up."""
    return {"status": "healthy", "service": "DentAI Pro API", "version": "2.0.0"}

@app.post("/auth/token", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Authenticate a clinic and return a JWT token.
    Use clinic_id as username and clinic password as password.
    """
    clinic = CLINICS.get(form_data.username)
    if not clinic or not verify_password(form_data.password, clinic["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect clinic ID or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        data={"sub": form_data.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": token, "token_type": "bearer"}

@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    clinic: dict = Depends(get_current_clinic)
):
    """
    Send a message to the DentAI agent.
    The agent handles booking, cancellation, rescheduling, and emergency routing.
    """
    import uuid
    session_id = request.session_id or str(uuid.uuid4())

    try:
        response, intent = run_dental_agent(
            message=request.message,
            session_id=session_id,
            clinic_schema=clinic["db_schema"]
        )
        return ChatResponse(
            response=response,
            session_id=session_id,
            intent_detected=intent
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {str(e)}"
        )

@app.get("/docs-info")
def api_docs_info():
    """Returns info about available endpoints — no auth required."""
    return {
        "endpoints": {
            "POST /auth/token": "Get JWT access token",
            "POST /chat": "Chat with DentAI agent (requires auth)",
            "GET /health": "Service health check",
            "GET /docs": "Interactive Swagger docs"
        },
        "demo_credentials": {
            "clinic_id": "demo_clinic",
            "password": "secret"
        }
    }
