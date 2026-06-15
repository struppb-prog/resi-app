# Resi La Playa — App de limpieza

App web que se conecta al Google Sheet de la residencia y muestra:
- Turnos de limpieza de la semana actual
- Estado de deudas por residente
- Alertas de stock de insumos
- Mensajes pre-armados para WhatsApp

---

## Setup paso a paso

### 1. Configurar Google Sheets API

1. Entrá a [console.cloud.google.com](https://console.cloud.google.com)
2. Creá un proyecto nuevo (o usá uno existente)
3. Buscá **"Google Sheets API"** → habilitala
4. Buscá **"Google Drive API"** → habilitala
5. Andá a **APIs & Services → Credentials**
6. Click **"+ Create Credentials" → Service Account**
   - Nombre: `resi-la-playa` (o cualquier cosa)
   - Click "Create and continue" → "Done"
7. En la lista de service accounts, click en la que creaste
8. Pestaña **"Keys"** → "Add Key" → "Create new key" → JSON
9. Descargá el archivo JSON → renombralo a **`credentials.json`**
10. Copialao a la carpeta de la app

11. **Compartí el Google Sheet con la service account:**
    - Abrí el archivo JSON y copiá el campo `client_email`
      (algo como `resi-la-playa@tu-proyecto.iam.gserviceaccount.com`)
    - Abrí el Google Sheet → Share → pegá ese email → rol "Viewer"

---

### 2. Instalar y configurar la app

```bash
# Clonar / copiar los archivos al servidor
cd /ruta/donde/pusiste/la/app

# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
nano .env   # Completar SHEET_ID, ADMIN_PIN, SECRET_KEY
```

**¿Dónde está el SHEET_ID?**
En la URL del Sheet: `docs.google.com/spreadsheets/d/**ESTE_ES_EL_ID**/edit`

---

### 3. Correr la app

**Desarrollo:**
```bash
source venv/bin/activate
python app.py
```

**Producción con gunicorn:**
```bash
source venv/bin/activate
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

**Con systemd (para que arranque solo):**
```ini
# /etc/systemd/system/resi-app.service
[Unit]
Description=Resi La Playa App
After=network.target

[Service]
User=tu-usuario
WorkingDirectory=/ruta/de/la/app
ExecStart=/ruta/de/la/app/venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 app:app
Restart=always
EnvironmentFile=/ruta/de/la/app/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable resi-app
sudo systemctl start resi-app
```

---

### 4. Agregar PINs a los residentes

1. Entrar a la app como admin (usuario: `admin`, PIN: el que pusiste en `.env`)
2. Ir a la pestaña **🔑 PINs**
3. Seleccionar el nombre del residente y asignarle un PIN
4. El residente ya puede entrar con su nombre + PIN

---

## Estructura de archivos

```
resi-app/
├── app.py              # Backend Flask
├── sheets_parser.py    # Conexión y parseo de Google Sheets
├── requirements.txt
├── .env                # Variables de entorno (no subir a git)
├── .env.example        # Template del .env
├── credentials.json    # Credenciales Google (no subir a git)
├── users.json          # PINs de residentes (se gestiona desde el admin)
└── templates/
    ├── login.html
    ├── resident.html
    └── admin.html
```

---

## Notas importantes

- El caché dura **1 hora**. Si actualizás el Sheet y querés ver los cambios inmediatamente, usá el botón **⟳ Actualizar datos** en el panel admin.
- Los PINs se guardan en `users.json` en texto plano. Para mayor seguridad en producción, usá hashes (bcrypt).
- `credentials.json` y `.env` **nunca deben subirse a Git**. Agregá al `.gitignore`:
  ```
  credentials.json
  .env
  users.json
  ```
