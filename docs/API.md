# API Guide

Authentication uses bearer JWT tokens.

```http
Authorization: Bearer <token>
```

## Login

```http
POST /api/auth/login
Content-Type: application/json

{
  "identifier": "admin@worldcup.ai",
  "password": "Admin@2026"
}
```

## Create Tournament

```http
POST /api/tournaments

{
  "name": "World Cup 2026",
  "sport": "football",
  "country": "Global",
  "start_date": "2026-06-11T18:00:00",
  "end_date": "2026-07-19T22:00:00"
}
```

## Add Team

```http
POST /api/tournaments/1/teams

{
  "name": "Brazil",
  "country": "Brazil",
  "flag": "🇧🇷",
  "ranking": 2,
  "home_advantage": 1
}
```

## Generate Fixtures

```http
POST /api/tournaments/1/fixtures
```

## Update Score

```http
PUT /api/matches/1/score

{
  "home_score": 2,
  "away_score": 1
}
```

## Submit Prediction

```http
POST /api/predictions

{
  "match_id": 3,
  "predicted_team_id": 1,
  "predicted_draw": false,
  "confidence": 70
}
```
