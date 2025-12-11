from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from core.database import get_db
from crud import radiomics as crud
from schemas.radiomics import RadiomicsCreate, Radiomics

router = APIRouter(prefix="/radiomics", tags=["Radiomics"])
from uuid import UUID

# ----------------------------------------------------------------------
# Get Radiomics by ID
# ----------------------------------------------------------------------
@router.get("/{radiomics_id}", response_model=Radiomics)
async def read_radiomics(radiomics_id: UUID, db: AsyncSession = Depends(get_db)):
    db_radiomics = await crud.get_radiomics(db, radiomics_id)
    if not db_radiomics:
        raise HTTPException(status_code=404, detail="Radiomics entry not found")
    return db_radiomics


# ----------------------------------------------------------------------
# Get Radiomics by Label UID
# ----------------------------------------------------------------------
@router.get("/by_label/{label_uid}", response_model=Radiomics)
async def read_radiomics_by_label(label_uid: str, db: AsyncSession = Depends(get_db)):
    db_radiomics = await crud.get_radiomics_by_label(db, label_uid)
    if not db_radiomics:
        raise HTTPException(status_code=404, detail="No radiomics entry found for this label UID")
    return db_radiomics


# ----------------------------------------------------------------------
# List All Radiomics Entries (Paginated)
# ----------------------------------------------------------------------
@router.get("/", response_model=List[Radiomics])
async def read_radiomics_list(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    radiomics_list = await crud.get_radiomics_list(db, skip=skip, limit=limit)
    return radiomics_list


# ----------------------------------------------------------------------
# Create a Radiomics Entry
# ----------------------------------------------------------------------
@router.post("/", response_model=Radiomics, status_code=status.HTTP_201_CREATED)
async def create_radiomics_entry(radiomics: RadiomicsCreate, db: AsyncSession = Depends(get_db)):
    return await crud.create_radiomics(db=db, radiomics=radiomics)


# ----------------------------------------------------------------------
# Delete a Radiomics Entry
# ----------------------------------------------------------------------
@router.delete("/{radiomics_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_radiomics_entry(radiomics_id: UUID, db: AsyncSession = Depends(get_db)):
    deleted = await crud.delete_radiomics(db, radiomics_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Radiomics entry not found")
    return None


# ----------------------------------------------------------------------
# Search / Filter Radiomics Entries
# ----------------------------------------------------------------------
@router.get("/search/", response_model=List[Radiomics])
async def search_radiomics_entries(
    feature_key: Optional[str] = Query(None, description="Search by feature key"),
    feature_value: Optional[str] = Query(None, description="Search by feature value"),
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    results = await crud.search_radiomics(
        db=db,
        feature_key=feature_key,
        feature_value=feature_value,
        skip=skip,
        limit=limit,
    )
    return results
