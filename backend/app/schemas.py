from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


Sport = Literal["FIFA World Cup", "UEFA", "AFC", "Volleyball", "Cricket", "Kabaddi"]
Confidence = Literal["Low", "Medium", "High"]


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr | None = None
    mobile: str | None = None
    country: str = "Global"
    password: str = Field(min_length=6)

    @field_validator("email", "mobile", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class LoginRequest(BaseModel):
    identifier: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class TournamentIn(BaseModel):
    name: str
    sport: str = "football"
    country: str = "Global"
    start_date: str | None = None
    end_date: str | None = None


class TeamIn(BaseModel):
    name: str
    country: str
    flag: str = "🏆"
    ranking: int = 50
    home_advantage: float = 0


class MatchIn(BaseModel):
    tournament_id: int
    home_team_id: int
    away_team_id: int
    game_no: str | None = None
    sport: Sport = "FIFA World Cup"
    round: str = "Group"
    match_date: str | None = None
    stadium: str = "TBD"
    result_mode: Literal["manual", "auto"] = "manual"
    external_match_id: str | None = None
    live_source: str | None = None


class ScoreIn(BaseModel):
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    result_mode: Literal["manual", "auto"] = "manual"


class MatchPredictionStatus(BaseModel):
    predictions_open: bool = True


class PredictionIn(BaseModel):
    match_id: int
    predicted_home_score: int = Field(ge=0)
    predicted_away_score: int = Field(ge=0)
    confidence_level: Confidence = "Medium"


class PredictionUpdate(BaseModel):
    predicted_home_score: int = Field(ge=0)
    predicted_away_score: int = Field(ge=0)
    confidence_level: Confidence = "Medium"


class EmailReportRequest(BaseModel):
    user_ids: list[int] = []
    select_all: bool = False
    subject: str = "WorldCup 2026 match result and predictions"
    message: str = "The latest result, predictions, and leaderboard reports are attached."


class RegistrationRequirements(BaseModel):
    email_required: bool = True
    mobile_required: bool = False
    otp_required: bool = False
