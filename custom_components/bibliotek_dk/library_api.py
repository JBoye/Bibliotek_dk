from __future__ import annotations

from bs4 import BeautifulSoup as BS
from dateutil import parser
import logging
import random
import re
import requests
import json

from .const import (
    HEADERS, JSON_HEADERS,
    URL_LOGIN_PAGE, ICON,
    USER_AGENTS,
)
DEBUG = True


_LOGGER: logging.Logger = logging.getLogger(__package__)
_LOGGER = logging.getLogger(__name__)


class Library:
    host, libraryName, icon, user = None, None, None, None
    loggedIn, eLoggedIn, running = False, False, False

    def __init__(
        self, userId: str, pincode: str, host=str, libraryName=None
    ) -> None:

        _LOGGER.info(f'{host}, {userId}, {pincode}')
        # Prepare a new session with a random user-agent
#        HEADERS["User-Agent"] = random.choice(USER_AGENTS)
#        _LOGGER.info(HEADERS["User-Agent"])

        self.session = requests.Session()
        self.session.headers = HEADERS
        self.json_header = JSON_HEADERS.copy()
        self.json_header["Origin"] = host
        self.json_header["Referer"] = host
        self.access_token = ''
        self.library_token = ''
        self.loggedIn = False

        self.host = host
        self.user = libraryUser(userId=userId, pincode=pincode)
        self.municipality = libraryName
        self.icon = ICON

    # The update function is called from the coordinator from Home Assistant
    def update(self):
        _LOGGER.debug(f"Updating ({self.user.userId[:-4]})")

        # Only one user can login at the time.
        self.running = True
        if self.login():
            # Only fetch user info once
            if not self.user.name:
                self.fetchUserInfo()

            # Fetch the states of the user
            self.fetchLoans()
            self.fetchReservations()
#            self.user.debts, self.user.debtsAmount = self.fetchDebts()

            # Logout
            self.logout()

            # Sort the lists
            self.sortLists()
        self.running = False
        return True

    #### PRIVATE BEGIN ####
    # Retrieve a webpage with either GET/POST
    def _fetchPage(self, url=str, payload=None, return_r=False):
        try:
            # If payload, use POST
            if payload:
                r = self.session.post(url, data=payload)
                _LOGGER.info(f'{url}, {payload}')

            # else use GET
            else:
                r = self.session.get(url)

            r.raise_for_status()

        except requests.exceptions.HTTPError as err:
            _LOGGER.error(f"HTTP Error while fetching {url}: {err}")
            # Handle the error as needed, e.g., raise it, log it, or notify the user.
            return None if return_r else None, None
        except requests.exceptions.Timeout:
            _LOGGER.error("Timeout fecthing (%s)", url)
            return None if return_r else None, None
        except requests.exceptions.TooManyRedirects:
            _LOGGER.error("Too many redirects fecthing (%s)", url)
            return None if return_r else None, None
        except requests.exceptions.RequestException as err:
            _LOGGER.error(f"Request Exception while fetching {url}: {err}")
            return None if return_r else None, None

        if return_r:
            return BS(r.text, "html.parser"), r

        # Return HTML soup
        return BS(r.text, "html.parser")

    def sortLists(self):
        # Sort the loans by expireDate and the Title
        self.user.loans.sort(key=lambda obj: (obj.expireDate is None, obj.expireDate, obj.title))
        # Sort the reservations
        self.user.reservations.sort(
            key=lambda obj: (
                obj.queueNumber is None,
                obj.queueNumber,
                obj.createdDate is None,
                obj.createdDate,
                obj.title,
            )
        )
        # Sort the reservations
        self.user.reservationsReady.sort(key=lambda obj: (obj.pickupDate is None, obj.pickupDate, obj.title))

    def _getIdInfo(self, material) -> tuple:
        try:
            value = material.input["value"]
            renewAble = "disabled" not in material.input.attrs
        except (AttributeError, KeyError) as err:
            _LOGGER.error(
                "Error in getting the Id and renewable on the material. Error: (%s)",
                err,
            )
        return value, renewAble

    def _getCoverUrl(self, id, typ='pid'):
        header = {**self.json_header, **{'Authorization': f'Bearer {self.library_token}', 'Accept': '*/*'}}
        res = self.session.get('https://cover.dandigbib.org/api/v2/covers',
                               params={'type': typ, 'identifiers': id, 'sizes': 'small'},
                               headers=header
                               )
        if res.status_code == 200:
            return res.json()[0]['imageUrls']['small']['url']
        return ''

    def _branchName(self, id):
        id = str(id).split('-')[-1]
        params = {
            'query': '\n query LibraryFragmentsSearch($q: String, $limit: PaginationLimitScalar, $offset: Int, $language: LanguageCodeEnum, $agencyId: String, $agencyTypes: [AgencyTypeEnum!]) {\n branches(q: $q, agencyid: $agencyId, language: $language, limit: $limit, offset: $offset, bibdkExcludeBranches:true, statuses:AKTIVE, agencyTypes: $agencyTypes) {\n hitcount\n result {\n agencyName\n agencyId\n branchId\n name }\n }\n }',
            'variables': {'language': "DA", 'limit': 2, 'q': id}
        }
        header = {**self.json_header, **{'Authorization': f'Bearer {self.access_token}', 'Accept': '*/*'}}
        res = self.session.post('https://fbi-api.dbc.dk/bibdk21/graphql', headers=header, data=json.dumps(params))
        if res.status_code == 200:
            data = res.json()['data']['branches']
            if data['hitcount'] == 1:
                return data['result'][0]['name']
        return id

    def _getDetails(self, faust):
        data = {}
        params = {
            "query": "\n    query getManifestationViaMaterialByFaust($faust: String!) {\n  manifestation(faust: $faust) {\n    ...ManifestationBasicDetails\n  }\n}\n    \n    fragment ManifestationBasicDetails on Manifestation {\n  ...WithLanguages\n  pid\n  titles {\n    full\n  }\n  abstract\n  materialTypes {\n    materialTypeSpecific {\n      display\n    }\n  }\n  creators {\n    display\n  }\n  edition {\n    publicationYear {\n      display\n    }\n  }\n  series {\n    title\n    members {\n      numberInSeries\n    }\n  }\n}\n    \n    fragment WithLanguages on Manifestation {\n  languages {\n    main {\n      display\n      isoCode\n    }\n  }\n}\n    ",
            "variables": {"faust": faust}
        }
        res = self.session.post("https://temp.fbi-api.dbc.dk/next-present/graphql", headers=self.json_header, data=json.dumps(params))
        if res.status_code == 200:
            data = res.json()['data']['manifestation']
        return data

    def _removeCurrency(self, amount) -> float:
        result = re.search(r"(\d*\,\d*)", amount)
        if result:
            amount = float(result.group(1).replace(",", "."))
        return amount

    # PRIVATE END  ####

    def login(self):
        self._get_tokens()

        if not self.loggedIn:
            # Fetch the loginpage and prepare a soup
            res = self.session.get(self.host + URL_LOGIN_PAGE)
            if res.status_code == 200:
                soup = BS(res.text, "html.parser")

            # Prepare the payload
            payload = {}
            # Find the <form>
            try:
                form = soup.find("form")
                for inputTag in form.find_all("input"):
                    # Fill the form with the userInfo
                    if inputTag["name"] in self.user.userInfo:
                        payload[inputTag["name"]] = self.user.userInfo[inputTag["name"]]
                    # or pass default values to payload
                    else:
                        payload[inputTag["name"]] = inputTag["value"]

                # Send the payload as POST and prepare a new soup
                # Use the URL from the response since we have been directed
                res2 = self.session.post(form["action"].replace("/login", res.url), data=payload)
                res2.raise_for_status()

            except (AttributeError, KeyError) as err:
                _LOGGER.error(
                    "Error processing the <form> tag and subtags (%s). Error: (%s)",
                    self.host + URL_LOGIN_PAGE,
                    err,
                )
            self._get_tokens()

        if DEBUG:
            _LOGGER.debug("(%s) is logged in: %s", self.user.userId[:-4], self.loggedIn)
        return self.loggedIn

    def _get_tokens(self):
        if not self.access_token or not self.loggedIn:
            res = self.session.get(f"{self.host}/dpl-react/user-tokens")
            if res.status_code == 200:
                self.library_token = res.text.split('"library"')[1].split('"')[1]
                self.loggedIn = '"user"' in res.text
                if self.loggedIn:
                    self.user_token = res.text.split('"user"')[1].split('"')[1]
                    self.json_header["Authorization"] = f"Bearer {self.user_token}"
        if not self.access_token:
            res = self.session.get('https://bibliotek.dk')
            if res.status_code == 200:
                self.access_token = res.text.split('"accessToken"')[1].split('"', 2)[1]

    def logout(self):
        url = self.host + "/user/logout"
        if self.loggedIn:
            # Fetch the logout page, if given a 200 (true) reverse it to false
            self.loggedIn = not self.session.get(url).status_code == 200
            if not self.loggedIn:
                self.session.close()
        if DEBUG:
            _LOGGER.debug(
                "(%s) is logged OUT @%s: %s",
                self.user.userId[:-4],
                url,
                not bool(self.loggedIn),
            )

    # Get information on the user
    def fetchUserInfo(self):
        # Fetch the user profile page
        res = self.session.get('https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/v2', headers=self.json_header)
        if res.status_code == 200:
            try:
                data = res.json()['patron']

                self.user.name = data['name']
                self.user.address = f'{data["address"]["street"]}\n{data["address"]["postalCode"]} {data["address"]["city"]}'
                self.user.phone = data['phoneNumber']
                self.user.phoneNotify = int(data['receiveSms'])
                self.user.mail = data['emailAddress']
                self.user.mailNotify = int(data['receiveEmail'])
                self.user.pickupLibrary = self._branchName(data['preferredPickupBranch'])
                self.libraryName = self._branchName(data['preferredPickupBranch'])
            except (AttributeError, KeyError) as err:
                _LOGGER.error(f"Error getting user info {self.user.userId}. Error: {err}")

    # Get the loans with all possible details
    def fetchLoans(self, soup=None) -> list:
        loans = []
        loansOverdue = []

        # Physical books
        res = self.session.get("https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/loans/v2", headers=self.json_header)
        if res.status_code == 200:
            _LOGGER.error(f'{self.user.name} {self.json_header["Authorization"]} {len(res.json())}')
            for material in res.json():
                faust = material['loanDetails']['recordId']
                data = self._getDetails(faust)
                data['CoverUrl'] = self._getCoverUrl(data['pid'])

                # Create an instance of libraryLoan
                obj = libraryLoan(data)

                # Renewable
                obj.renewId = material['loanDetails']['loanId']
                obj.renewAble = material['isRenewable']

                # # URL and image
                # obj.coverUrl = self._getCoverUrl(data['pid'])

                # # Type, title and creator
                # obj.title = data['titles']['full'][0]
                # obj.creators = data['creators'][0]['display']
                # obj.type = data['materialTypes'][0]['materialTypeSpecific']['display']

                # Details
                obj.loanDate = parser.parse(material['loanDetails']['loanDate'], ignoretz=True)
                obj.expireDate = parser.parse(material['loanDetails']['dueDate'], ignoretz=True)
                obj.id = material['loanDetails']['materialItemNumber']

                # Add the loan to the stack
                loans.append(obj)

        # Ebooks
        res = self.session.get('https://pubhub-openplatform.dbc.dk/v1/user/loans', headers=self.json_header)
        if res.status_code == 200:
            edata = res.json()

            self.user.eBooks = edata['userData']['totalEbookLoans']
#            self.user.eBooksQuota = edata['userData']['ebookLoansRemaining'] + self.user.eBooks
            self.user.eBooksQuota = edata['libraryData']['maxConcurrentEbookLoansPerBorrower']
            self.user.audioBooks = edata['userData']['totalAudioLoans']
#            self.user.audioBooksQuota = edata['userData']['audiobookLoansRemaining'] + self.user.audioBooks
            self.user.audioBooksQuota = edata['libraryData']['maxConcurrentAudiobookLoansPerBorrower']

            for material in edata['loans']:
                id = material['libraryBook']['identifier']
                res2 = self.session.get(f'https://pubhub-openplatform.dbc.dk/v1/products/{id}', headers=self.json_header)
                if res2.status_code == 200:
                    data = res2.json()['product']
                    # Create an instance of libraryLoan
                    obj = libraryLoan(data)
                    obj.id = id

                    # # URL and image
                    # obj.coverUrl = data['thumbnailUri']

                    # # Type, title and creator
                    # obj.title = data['title']
                    # obj.creators = ' og '.join([item['firstName'] + item['lastName'] for item in data['contributors']])
                    # obj.type = data['format']

                    # Details
                    obj.loanDate = parser.parse(material['orderDateUtc'], ignoretz=True)
                    obj.expireDate = parser.parse(material['loanExpireDateUtc'], ignoretz=True)
                    loans.append(obj)
        self.user.loans = loans
        self.user.loansOverdue = loansOverdue

    # def fetchLoansOverdue(self) -> list:
    #     if DEBUG:
    #         _LOGGER.debug("%s, Reusing the fetchLoans function", self.user.name)
    #     # Fetch the loans overdue page
    #     return self.fetchLoans(self._fetchPage(self.host + URLS[LOANS_OVERDUE]))

    # Get the current reservations
    def fetchReservations(self, soup=None) -> list:
        reservations = []
        reservationsReady = []

        # Physical
        res = self.session.get("https://fbs-openplatform.dbc.dk/external/v1/agencyid/patrons/patronid/reservations/v2", headers=self.json_header)
        materials = {item['transactionId']: item for item in res.json()}  # make sure only to take last if more than one item with same transaction
        for material in materials.values():
            # Get the first element (id)
            id = material['recordId']
            data = self._getDetails(id)
            data['CoverUrl'] = self._getCoverUrl(data['pid'])

            if material['state'] == 'readyForPickup':
                obj = libraryReservationReady(data)
            else:
                obj = libraryReservation(data)
            obj.id = id
            # # URL and image
            # obj.coverUrl = self._getCoverUrl(data['pid'])

            # # Type, title and creator
            # obj.title = data['titles']['full'][0]
            # obj.creators = data['creators'][0]['display']
            # obj.type = data['materialTypes'][0]['materialTypeSpecific']['display']

            # Details
            obj.createdDate = parser.parse(material['dateOfReservation'], ignoretz=True)
            obj.pickupLibrary = self._branchName(material['pickupBranch'])
            if material['state'] == 'readyForPickup':
                obj.reservationNumber = material['pickupNumber']
                obj.pickupDate = parser.parse(material['pickupDeadline'], ignoretz=True)
                reservationsReady.append(obj)
            else:
                obj.expireDate = parser.parse(material['expiryDate'], ignoretz=True)
                obj.queueNumber = material['numberInQueue']
                reservations.append(obj)

        res = self.session.get("https://pubhub-openplatform.dbc.dk/v1/user/reservations", headers=self.json_header)
        if res.status_code == 200:
            edata = res.json()
            for material in edata['reservations']:
                id = material['libraryBook']['identifier']
                res2 = self.session.get(f'https://pubhub-openplatform.dbc.dk/v1/products/{id}', headers=self.json_header)
                if res2.status_code == 200:
                    data = res2.json()['product']

                    obj = libraryReservation(data)
                    obj.id = id

                    # # URL and image
                    # obj.coverUrl = data['thumbnailUri']

                    # # Type, title and creator
                    # obj.title = data['title']
                    # obj.creators = ' og '.join([item['firstName'] + item['lastName'] for item in data['contributors']])
                    # obj.type = data['format']

                    # # Details TODO
                    # obj.expireDate = parser.parse(data['expiryDate'])
                    # obj.createdDate = parser.parse(data['dateOfReservation'])
                    # obj.queueNumber = data['numberInQueue']
                    # obj.pickupLibrary = data['pickupBranch']
                    reservations.append(obj)
        self.user.reservations = reservations
        self.user.reservationsReady = reservationsReady

    # Get debts, if any, from the Library
    def fetchDebts(self) -> tuple:
        params = {'includepaid':'false', 'includenonpayable':'true'}
        res = self.session.get("https://fbs-openplatform.dbc.dk/external/agencyid/patron/patronid/fees/v2", params=params, headers=self.json_header)

        tempList = []
        # From the <div> with containg the class of the materials
        for material in self._getMaterials(soup):
            obj = libraryDebt()

            # Get the first element (id)
            # obj.id = self._getIdInfo(material)[0] # This actuallly serves no purpose for debts

            # URL and image
            obj.url, obj.coverUrl = self._getMaterialUrls(material)

            # Type, title and creator
            obj.title, obj.creators, obj.type = self._getMaterialInfo(material)

            # Details
            for keys, value in self._getDetails(material):
                if "fee-date" in keys:
                    obj.feeDate = self._getDatetime(value)
                elif "fee-type" in keys:
                    obj.feeType = value
                elif "fee_amount" in keys:
                    obj.feeAmount = self._removeCurrency(value)

            tempList.append(obj)

        try:
            amount = soup.select_one("span[class='amount']")
            amount = self._removeCurrency(amount.string) if amount else 0.0
        except (AttributeError, KeyError) as err:
            _LOGGER.error("Error processing the debt amount. Error: (%s)", err)

        if DEBUG:
            _LOGGER.debug(
                "%s has %s debts with a total of {amount}",
                self.user.name,
                len(tempList),
            )

        return tempList, amount


class libraryUser:
    userInfo = None
    name, address = None, None
    phone, phoneNotify, mail, mailNotify = None, None, None, None
    loans, loansOverdue, reservations, reservationsReady, debts = [], [], [], [], []
    debtsAmount = 0.0
    eBooks, eBooksQuota, audioBooks, audioBooksQuota = 0, 0, 0, 0
    pickupLibrary = None

    def __init__(self, userId: str, pincode: str) -> None:
        self.userInfo = {"loginBibDkUserId": userId, "pincode": pincode}
        self.userId = userId


class libraryMaterial:
    id = None
    type, title, creators = None, None, None
    url, coverUrl = None, None

    def __init__(self, data):
        try:
            if 'thumbnailUri' in data:
                # from ereol
                self.coverUrl = data['thumbnailUri']
                self.title = data['title']
                self.creators = ' og '.join([item['firstName'] + item['lastName'] for item in data['contributors']])
                self.type = data['format']
            else:
                # physical book
                self.coverUrl = data['CoverUrl']
                self.title = data['titles']['full'][0]
                if data['creators']:
                    self.creators = data['creators'][0]['display']
                self.type = data['materialTypes'][0]['materialTypeSpecific']['display']
        except Exception as err:
            _LOGGER.error(f'Failed to set material data, {err}')
            _LOGGER.error(f'{data}')


class libraryLoan(libraryMaterial):
    loanDate, expireDate = None, None
    renewId, renewAble = None, None


class libraryReservation(libraryMaterial):
    createdDate, expireDate, queueNumber = None, None, None
    pickupLibrary = None


class libraryReservationReady(libraryMaterial):
    createdDate, pickupDate, reservationNumber = None, None, None
    pickupLibrary = None


class libraryDebt(libraryMaterial):
    feeDate, feeType, feeAmount = None, None, None
