
# Python Django 🐍

██████╗░░░░░░██╗░█████╗░███╗░░██╗░██████╗░░█████╗░
██╔══██╗░░░░░██║██╔══██╗████╗░██║██╔════╝░██╔══██╗
██║░░██║░░░░░██║███████║██╔██╗██║██║░░██╗░██║░░██║
██║░░██║██╗░░██║██╔══██║██║╚████║██║░░╚██╗██║░░██║
██████╔╝╚█████╔╝██║░░██║██║░╚███║╚██████╔╝╚█████╔╝
╚═════╝░░╚════╝░╚═╝░░╚═╝╚═╝░░╚══╝░╚═════╝░░╚════╝░

# Python Django

## Installation

1. Clone this repository.
2. Create a .env file with the example as in the .env.example file. Then, customize it with your database configuration
3. Then, first make a venv.

   ```sh
   python -m venv env
   ```

4. Then activate venv with :

   If using Powershell :

   ```sh
   env\Scripts\activate
   ```

   If using Bash :

   ```sh
   source env/Scripts/activate
   ```

5. Then, install the required dependencies provided in the requirements.txt file with the command :

   ```sh
   pip install -r requirements.txt
   ```

8. Run the app with the command :

   ```sh
   python manage.py runserver
   ```

## HTTPS (local dev)

Use HTTPS on the Django dev server:

1. Install mkcert

macOS

brew install mkcert
brew install nss   # wajib untuk Firefox


Linux

sudo apt install libnss3-tools
curl -JLO https://dl.filippo.io/mkcert/latest?for=linux/amd64
chmod +x mkcert
sudo mv mkcert /usr/local/bin/

2. Setup local CA
mkcert -install

3. Generate cert
mkdir -p certs
mkcert -key-file certs/localhost.key -cert-file certs/localhost.crt localhost 127.0.0.1 ::1

```sh
python3 manage.py runserver_plus --cert-file certs/localhost.crt --key-file certs/localhost.key
```

9. To deactivate venv, simply type :

   ```sh
   deactivate
   ```

## Development Note

If you download a new package, please update the requirements.txt file with the command below.

   ```sh 
   pip freeze > requirements.txt
   ```

## Accesspoint

   ```sh
   1. Test
   ```
