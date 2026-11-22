# AnnaSetu

### Where Surplus Meets the Hungry

**AnnaSetu** is a food surplus rescue and donation platform that connects food donors, NGOs, and volunteers to reduce food waste and help people in need.

## Live Demo

**Live Website:https://annasetu-xsxf.onrender.com

**GitHub Repository:** https://github.com/Nitinn12/AnnaSetu

## Problem Statement

Every day, large quantities of edible food go to waste while many people struggle to access nutritious meals.

AnnaSetu aims to bridge this gap by providing a platform where surplus food can be shared with organizations that distribute it to people in need.

## Our Solution

AnnaSetu creates a simple digital connection between:

- **Food Donors:** Restaurants, event organizers, and individuals who have surplus food.
- **NGOs:** Organizations that discover and claim available food donations.
- **Volunteers:** People who help collect and deliver donated food.
- **Administrators:** Manage platform activities and assignments.

## Key Features

- User registration and role-based authentication.
- Food surplus donation creation and management.
- NGOs can discover and claim available donations.
- Claim and release management.
- Volunteer assignment and delivery tracking.
- Donation status history.
- Impact dashboard with food rescue statistics.
- Food safety information.
- Responsive user interface.

## How It Works

1. **Donate:** A donor posts details about surplus food.
2. **Discover:** NGOs explore available food donations.
3. **Claim:** An eligible NGO claims a donation.
4. **Assign:** A volunteer is assigned for pickup and delivery.
5. **Deliver:** The volunteer updates the collection and delivery status.
6. **Track Impact:** The platform displays food rescue and donation statistics.

## Tech Stack

| Category | Technologies |
|---|---|
| Backend | Python, Flask |
| Frontend | HTML, CSS, Bootstrap 5, JavaScript |
| Database | SQLite, PostgreSQL |
| ORM | Flask-SQLAlchemy, SQLAlchemy |
| Migrations | Flask-Migrate, Alembic |
| Deployment | Render |
| Version Control | Git, GitHub |

## Project Structure

```text
AnnaSetu/
├── app/
│   ├── auth/
│   ├── donations/
│   ├── receivers/
│   ├── volunteers/
│   ├── impact/
│   ├── matching/
│   ├── tracking/
│   ├── templates/
│   └── static/
├── migrations/
├── tests/
├── instance/
├── requirements.txt
├── requirements-dev.txt
├── Procfile
└── run.py
```

## Local Installation

**1. Clone the repository**

```bash
git clone https://github.com/Nitinn12/AnnaSetu.git
cd AnnaSetu
```

**2. Create a virtual environment**

```bash
python -m venv .venv
```

**3. Activate the environment**

Windows:

```powershell
.venv\Scripts\Activate.ps1
```

**4. Install dependencies**

```bash
pip install -r requirements.txt
```

**5. Configure environment variables**

Create a `.env` file:

```env
SECRET_KEY=your-secret-key
FLASK_DEBUG=1
DATABASE_URL=
```

**6. Apply database migrations**

```bash
flask --app run db upgrade
```

**7. Start the application**

```bash
python run.py
```

Open: `http://127.0.0.1:5000`

## Testing

Run the test suite:

```bash
python -m pytest tests -q
```

## Our Impact

AnnaSetu aims to:

- Reduce avoidable food waste.
- Connect surplus food with people who need it.
- Support NGOs and community organizations.
- Encourage volunteer participation.
- Build a more sustainable food-sharing ecosystem.

## Future Scope

- Smarter location-based donor and NGO matching.
- AI-assisted food donation recommendations.
- Real-time pickup and delivery notifications.
- Interactive donation maps.
- Advanced analytics and impact reporting.

## Hackathon

**ACE BUILDERS HACKATHON 2026**

**Problem Statement 2: Rescue the Surplus**

**AnnaSetu — Where Surplus Meets the Hungry.**
