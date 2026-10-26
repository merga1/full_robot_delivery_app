# Local deployment

1. Copy `backend/.env.example` to `backend/.env` and replace the development secrets.
2. Start the service:

```powershell
docker compose up --build
```

3. Open the backend docs at [http://localhost:8000/docs](http://localhost:8000/docs), or use:

- [Customer](http://localhost:8000/customer/)
- [Kitchen](http://localhost:8000/kitchen/)
- [Admin](http://localhost:8000/admin/)
- [Robot screen](http://localhost:8000/robot-screen/)

The container runs the migration and idempotent seed before starting FastAPI. The seed creates development accounts, menu items, five public table codes, and `robot_01`; it intentionally leaves table destinations empty until real map poses are measured.

The administrator can manage foods, drinks and tables from the Admin page. Menu items support price, category, description, preparation notes, availability, sort order, image URL, and direct photo upload (JPG, PNG or WEBP up to 5 MB). Uploaded images are kept in the `restaurant-web-uploads` Docker volume and are served from `/assets/uploads/`. The supplied sample food photos are copied into `web/assets/` and linked by the seed data.

For a development server without Docker, from `backend` run `alembic upgrade head`, `python -m app.seed`, then `uvicorn app.main:app --reload`.
