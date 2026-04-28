# JupyterHealth Exchange
JupyterHealth Exchange is a Django web application that facilitates sharing patient-consented health data with authorized users via a Web UI and REST, MCP, and FHIR APIs.

<p align="center">
	<img src="doc/jupyterhealth-exchange.png" width="400">
</p>
Features include:

- OAuth 2.0, OIDC using [django-oauth-toolkit](https://github.com/jazzband/django-oauth-toolkit) and [grafana-django-saml2-auth](https://github.com/grafana/django-saml2-auth) for SAML/SSO
- Simple Role Based Access Control
- FHIR R5 validation using [fhir.resources](https://github.com/glichtner/fhir.resources)
- [Open mHealth](https://www.openmhealth.org/) validation using JSON schemas
- REST APIs using [Django Rest Framework](https://github.com/encode/django-rest-framework)
- Built-in, light-weight Vanilla JS SPA UI (npm not required) using [oidc-clinet-ts](https://github.com/authts/oidc-client-ts), [handlebars](https://github.com/handlebars-lang/handlebars.js) and [bootstrap](https://github.com/twbs/bootstrap)

---


## Documentation

- https://jupyterhealth.org/software-documentation/


## What It Does

##### Typical User Flow

Researchers create studies and recruit patients, who consent and submit observations via client applications, and the data is then stored in JupyterHealth Exchange and queried by researchers using Jupyter Notebooks or other systems.

<p align="center">
	<img src="doc/jupyterhealth-exchange-user_flow.jpg" height="400">
</p>

**Typical Data Flow**

Users manage the system via the Web UI, and data producers receive invitation credentials by email, manage consents through the Admin API, and upload data to JupyterHealth Exchange using the FHIR API. Data consumers such as Jupyter Notebooks or other systems then query and read the data through REST and MCP APIs.

<p align="center">
	<img src="doc/jupyterhealth-exchange-data_flow.jpg" height="400">
</p>


## Getting Started

> [!NOTE]
> Getting started with Docker is in the works!

1. Set up your Python environment and install dependencies from `Pipfile` - this project uses Django **version 5.2** which requires python  **3.12**
    - NB: If using pipenv it is recommended to run `pipenv sync` against the lock file to match package versions

1. Create a new Postgres DB (currently only Postgres is supported)

1. Copy `dot_env_example.txt` to `.env` and update the `DB_*` parameters to match (2) above.

   -  Optionally you can add a Django `SECRET_KEY` by running the command below or you can leave this for now to use a randomly generated value at runtime (this will not work with more than one worker)
      `$ openssl rand -base64 32`

1. Ensure the `.env` is loaded into your Python environment, eg for pipenv run `$ pipenv shell`

1. Run the Django migration `$ python manage.py migrate` to create the database tables.

1. Seed the database by running the Django management command `$ python manage.py seed`

1. Start the server with `$ python manage.py runserver`

1. Browse to http://localhost:8000/admin and enter the credentials `sam@example.com` `Jhe1234!`

1. Under *Django OAuth Toolkit* > *Applications* you should see the seeded OAuth2 application named `JHE Admin UI` with a Redirect URI for `http://localhost:8000/auth/callback` - this is used for the Web UI OAuth 2.0 login.

1. Click the LOG OUT button at the top

1. Finally, we need to create an RS256 Private Key for signing the JWT

      - Run `openssl genrsa -out oidc.key 4096`
      - Run `awk '{printf "%s%s", (NR==1?"":"\\n"), $0}' oidc.key` to remove line breaks
        **Note: some python environments and OS combinations do not handle the "\n" so you may need to include line breaks in the `.env` file.**

      - Return to the `.env` file and update the `OIDC_RSA_PRIVATE_KEY`
      - Keep the `oidc.key` somewhere safe

1. Browse to http://localhost:8000/ and log in with the credentials `mary@example.com` `Jhe1234!` and you should be directed to the `/portal/organizations` path with some example Organizations is the dropdown

1. New users can be signed up from the base URL (eg http://localhost:8000/) with the default invitation code "jhe" which is set from the `REGISTRATION_INVITE_CODE` in `.env`

> [!NOTE]
> Due to browser security restrictions and the [oidc-client-ts](https://github.com/authts/oidc-client-ts) used for authentication, the web app **must be accessed over HTTPS for any hostname other than localhost** - see [Running in Production](#running-in-production) below.



## Who and What the App Manages

Entities are based on the [HL7 FHIR model](https://build.fhir.org/), a widely used healthcare standard for organizing and exchanging clinical information between systems.

### Patients & Practitioners

- Any user accessing the Web UI is a [Practitioner](https://build.fhir.org/practitioner.html) by default. In practice this might be a researcher, a clinician, an administrator or even an individual setting up JupyterHealth Exchange to view their own health data.
- [Patient](https://build.fhir.org/patient.html) users are registered by Practitioners and are sent a link to authenticate and upload data.
- The same OAuth 2.0 strategy is used for both Practitioners and Patients, the only difference being that the authorization code is provided out-of-band for Patients (ie invitation links).

### Organizations

- An [Organization](https://build.fhir.org/organization.html) is a group of Practitioners, Patients and Studies (FHIR Groups) and is used to manage access to data.
- An Organization is typically hierarchical with sub-Organizations like Institutions, Departments, Labs etc.
- A Practitioner belongs to one or more Organization.
- A Patient belongs to one or more Organization.
- A Study belongs to one single Organization.

### Studies

- A Study is a [Group](https://build.fhir.org/group.html) of Patients and belongs to a single Organization.
- A Study has one or more Clients (apps that talk to JupyterHealth Exchange), one or more matching Data Sources (anything that produces data) and one or more Scope Requests (eg Blood Pressure, Heart Rate, etc)
- When a Patient is added to a Study, they must explicitly consent to sharing the requested Scopes before any personal data (Observations) can be uploaded or shared.

### Observations

- An [Observation](https://www.hl7.org/fhir/observation.html) is Patient data and belongs to a single Patient.
- An Observation must reference a Patient ID as the *subject* and a Data Source ID as the *device*.
- Personal device data is expected to be in the [Open mHealth](https://www.openmhealth.org/documentation/#/overview/get-started) (JSON) format however the system can be easily extended to support any binary data attachments or discrete Observation records.
- Observation data is stored as a *valueAttachment* in Base 64 encoded JSON binary.
- Authorization to view Observations depends on the relationship of Organization, Study and Scopes/consents as described above.

### Data Sources

- A Data Source is anything that produces Observations (typically a device app eg iHealth). An Observation references a Data Source ID in the *device* field.
- A Data Source supports one or more Scopes (types) of Observations (eg Blood Glucose).
- A Study has one or more associated Data Sources.
-  A Data Source has one Client. In some cases a single app may be both a Data Source and a Client, in which case a record is created for each and both are added to the Study.

### Clients

- Clients are apps that talk to JupyterHealth Exchange.
- Each Client has its own OAuth 2.0 Client ID.
- A Study has one or more associated Clients.
- A Client has one or more associated Data Sources. In some cases a single app may be both a Data Source and a Client, in which case a record is created for each and both are added to the Study.

## Quick Start Walkthrough

1. Sign up as a new user from the Web UI.
2. Create a new Organization (your user is automatically added to the Organization with a Manager role).
3. Create a new Study for the Organization (View Organization → Studies+).
4. Create a new Patient for the Organization using a different email than step 1 (Patients → Add Patient).
5. Add Data Sources and Scopes to the Study (View Study → Data Sources+, Scope Requests+).
6. Add the Patient to the Study (Patients → Select patient → Add Patient(s) to Study).
7. Create an Invitation Link for the Patient (View Patient → Generate Invitation Link).
8. Use the code in the invitation link with the Auth API to exchange it for an access token.
9. Upload Observations using the FHIR API and access token.
10. View the Observations from the Web UI.

## Contributing

See [doc](https://jupyterhealth.org/software-documentation/) for test requirements, coding standards, and PR checklist.