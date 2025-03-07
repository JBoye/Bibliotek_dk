from __future__ import annotations

from bs4 import BeautifulSoup as BS
from dateutil import parser
import logging
import re
import requests
import json

from .const import (
    HEADERS, JSON_HEADERS,
    URL_LOGIN_PAGE,
    status_query, details_query
)
DEBUG = True


_LOGGER: logging.Logger = logging.getLogger(__package__)
_LOGGER = logging.getLogger(__name__)


class Library:
    host, libraryName, icon, user = None, None, None, None
    loggedIn, running = False, False

    def __init__(
        self, userId: str, pincode: str, host: str, agency: str, libraryName=None
    ) -> None:

        self.session = requests.Session()
        self.session.headers = HEADERS

        self.json_header = JSON_HEADERS.copy()
        self.json_header["Origin"] = host
        self.json_header["Referer"] = host
        self.access_token = ''
        self.access_token2 = ''
        self.library_token = ''
        self.agencies = {}
        self.loggedIn = ''

        self.host = host
        self.agency = agency
        self.user = libraryUser(userId=userId, pincode=pincode)
        self.user.date = self.user.userId[:-4]
        self.municipality = libraryName
        self.use_national = False

    # The update function is called from the coordinator from Home Assistant
    def update(self):
        _LOGGER.debug(f"Updating ({self.user.userId[:-4]})")
        status = {}

        # Only one user can login at the time.
#        self.running = True

        status = {'loans': [], 'orders': [], 'debt': []}
        # physical books from bibliotek.dk
        if self.use_national:
            if self.login(local_site=False):
                status = self.fetchPhysicalStatus()
                self.logout()
                if debt := status['debt']:
                    _LOGGER.error(f"bibliotek.dk debt data: {debt}")

        # from local library for ereolen books
        if self.login(local_site=True):
            # Only fetch user info once
            if not self.user.name:
                self.fetchUserInfo()

            # Fetch the states of the user
            self.fetchLoans(status['loans'])
            self.fetchReservations(status['orders'])
            self.fetchDebts(status['debt'])

            # Logout
            self.logout()

            # Sort the lists
            self.sortLists()
#        self.running = False
        return True

    # PRIVATE BEGIN ####
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
        if id in self.agencies:
            return self.agencies[id]

        params = {
            'query': '\n query LibraryFragmentsSearch($q: String, $limit: PaginationLimitScalar, $offset: Int, $language: LanguageCodeEnum, $agencyId: String, $agencyTypes: [AgencyTypeEnum!]) {\n branches(q: $q, agencyid: $agencyId, language: $language, limit: $limit, offset: $offset, bibdkExcludeBranches:true, statuses:AKTIVE, agencyTypes: $agencyTypes) {\n hitcount\n result {\n agencyName\n agencyId\n branchId\n name }\n }\n }',
            'variables': {'language': "DA", 'limit': 2, 'q': id}
        }
        header = {**self.json_header, **{'Authorization': f'Bearer {self.access_token}', 'Accept': '*/*'}}
        res = self.session.post('https://fbi-api.dbc.dk/bibdk21/graphql', headers=header, json=params)
        if res.status_code == 200:
            data = res.json()['data']['branches']
            if data['hitcount'] == 1:
                return data['result'][0]['name']
        return id

    def _getDetails(self, faust):
        data = {}
        params = {"query": details_query, "variables": {"faust": faust}}
        url = self.urls.get('data-fbi-global-base-url', "https://temp.fbi-api.dbc.dk/next-present/graphql")
        res = self.session.post(url, headers=self.json_header, json=params)
        if res.status_code == 200:
            data = res.json()['data']
        else:
            _LOGGER.error(f"Error getting details for material: '{faust}'")
        return data

    # PRIVATE END  ####

    def login(self, local_site=False):
        if not self.loggedIn:
            if local_site:
                url = self.host + URL_LOGIN_PAGE
            else:
                url = self._get_login_url()

            res = self.session.get(url)
            if res.status_code != 200:
                _LOGGER.error("f({self.user.date}) Failed to login to {url}")
                return

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

#            except (AttributeError, KeyError) as err:
            except Exception as err:
                _LOGGER.error(f"Error processing the <form> tag and subtags ({url}). Error: ({err})")

            if local_site:
                self._get_tokens()
            else:
                self.access_token2 = re.search(r'"accessToken":"([^"]*)"', res2.text).group(1)
                if res2.url == f'https://bibliotek.dk/?setPickupAgency={self.agency}':
                    self.loggedIn = 'https://bibliotek.dk?message=logout'
            if DEBUG:
                _LOGGER.debug("(%s) is logged in: %s", self.user.userId[:-4], self.loggedIn)
        return self.loggedIn

    def _get_tokens(self):
        if not self.library_token:
            res = self.session.get(f"{self.host}/dpl-react/user-tokens")
            if res.status_code == 200:
                self.library_token = res.text.split('"library"')[1].split('"')[1]
                if '"user"' in res.text:
                    self.loggedIn = self.host + '/logout'
                    self.user_token = res.text.split('"user"')[1].split('"')[1]
                    self.json_header["Authorization"] = f"Bearer {self.user_token}"
        if not self.access_token:
            res = self.session.get('https://bibliotek.dk')
            if res.status_code == 200:
                self.access_token = res.text.split('"accessToken"')[1].split('"', 2)[1]

    def _get_login_url(self):
        header = {'Host': "bibliotek.dk", "Accept": "*/*", "Accept-Encoding": "gzip, deflate, br, zstd", "Content-Type": "application/json"}
        res = self.session.get('https://bibliotek.dk/api/auth/providers', headers=header)
        res.raise_for_status()
        res2 = self.session.get('https://bibliotek.dk/api/auth/csrf', headers=header)
        res.raise_for_status()

        params = {
            'csrfToken': res2.json()['csrfToken'],
            'callbackUrl': f'https://bibliotek.dk/?setPickupAgency={self.agency}',
            'json': 'true',
        }
        res = self.session.post(f'https://bibliotek.dk/api/auth/signin/adgangsplatformen?agency={self.agency}&force_login=1', json=params, headers=header)
        res.raise_for_status()
        return res.json()['url']

    def logout(self):
        if self.loggedIn:
            url = self.loggedIn
            # Fetch the logout page, if given a 200 (true) reverse it to false
            self.loggedIn = not self.session.get(url).status_code == 200
            if not self.loggedIn:
                self.access_token = ''
                self.access_token2 = ''
                self.user_token = ''
                self.library_token = ''
                self.session.close()
                self.session = requests.Session()
                self.session.headers = HEADERS
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
                _LOGGER.error(f"Error getting user info {self.user.dat}. Error: {err}")

    def fetchPhysicalStatus(self):
        header = {
            'Authorization': f'Bearer {self.access_token2}',
            'Host': "fbi-api.dbc.dk", 
            'Referer': "https://bibliotek.dk/", 
            'Origin': "https://bibliotek.dk/",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
        }
        js = {
            'query': status_query,
            'variables': [],
        }
        js = json.loads("{\"query\":\"\\n    query BasicUser {\\n      user {\\n        name\\n        mail\\n        address\\n        postalCode\\n        isCPRValidated\\n        loggedInAgencyId\\n        loggedInBranchId\\n        municipalityAgencyId\\n        omittedCulrData {\\n          hasOmittedCulrUniqueId\\n          hasOmittedCulrMunicipality\\n          hasOmittedCulrMunicipalityAgencyId\\n          hasOmittedCulrAccounts\\n        }\\n        rights {\\n          infomedia \\n          digitalArticleService \\n          demandDrivenAcquisition\\n        }\\n        agencies {\\n          id\\n          name\\n          type\\n          hitcount\\n          user {\\n            mail\\n          }\\n          result {\\n            branchId\\n            agencyId\\n            agencyName\\n            agencyType\\n            name\\n            branchWebsiteUrl\\n            pickupAllowed\\n            borrowerCheck\\n            culrDataSync\\n          }\\n        }\\n        debt {\\n            title\\n            amount\\n            creator\\n            date\\n            currency\\n            agencyId\\n        }\\n        loans {\\n          agencyId\\n          loanId\\n          dueDate\\n          title\\n          creator\\n          manifestation {\\n            pid\\n            ...manifestationTitleFragment\\n            ownerWork {\\n              workId\\n            }\\n            creators {\\n              ...creatorsFragment\\n            }\\n            materialTypes {\\n              ...materialTypesFragment\\n            }\\n            cover {\\n              thumbnail\\n            }\\n            recordCreationDate\\n          }\\n        }\\n        orders {\\n          orderId\\n          status\\n          pickUpBranch {\\n            agencyName\\n            agencyId\\n          }\\n          pickUpExpiryDate\\n          holdQueuePosition\\n          creator\\n          orderType\\n          orderDate\\n          title\\n          manifestation {\\n            pid\\n            ...manifestationTitleFragment\\n            ownerWork {\\n              workId\\n            }\\n            creators {\\n              ...creatorsFragment\\n            }\\n            materialTypes {\\n              ...materialTypesFragment\\n            }\\n            cover {\\n              thumbnail\\n            }\\n            recordCreationDate\\n          }\\n        }   \\n      }\\n    }\\n    fragment creatorsFragment on CreatorInterface {\\n  ... on Corporation {\\n    __typename\\n    display\\n    nameSort\\n    roles {\\n      function {\\n        plural\\n        singular\\n      }\\n      functionCode\\n    }\\n  }\\n  ... on Person {\\n    __typename\\n    display\\n    nameSort\\n    roles {\\n      function {\\n        plural\\n        singular\\n      }\\n      functionCode\\n    }\\n  }\\n}\\n    fragment manifestationTitleFragment on Manifestation {\\n  titles {\\n    main\\n    full\\n  }\\n}\\n    fragment materialTypesFragment on MaterialType {\\n  materialTypeGeneral {\\n    code\\n    display\\n  }\\n  materialTypeSpecific {\\n    code\\n    display\\n  }\\n}\",\"variables\":{}}")

        res = self.session.post('https://fbi-api.dbc.dk/bibdk21/graphql', json=js, headers=header)
        if res.status_code == 200:
            data = res.json()['data']['user']
            self.agencies = {item['branchId']: item['name'] for item in data['agencies'][0]['result']}
            return {key: data[key] for key in ['debt', 'loans', 'orders']}
        else:
            return {key: [] for key in ['debt', 'loans', 'orders']}

    # Get the loans with all possible details
    def fetchLoans(self, physical=[]):
        res = self.session.get(f'{self.host}/user/me/loans')
        self.urls = {}
        if res.status_code == 200:
            self.urls = {m[0]: m[1] for m in re.findall(r'(data-[a-zA-Z0-9\-\_]+-url)="([^"]*)"', res.text)}
        loans = []
        loansOverdue = []

        # Physical books
        if self.use_national:
            for data in physical:
                # Create an instance of libraryLoan
                obj = libraryLoan(data)

                # Renewable
                obj.renewId = data['loanId']
    #            obj.renewAble = material['isRenewable']
    #            obj.loanDate = parser.parse(material['loanDetails']['loanDate'], ignoretz=True)
                obj.expireDate = parser.parse(data['dueDate'], ignoretz=True)
                obj.id = data['manifestation']['pid']
                loans.append(obj)
        else:
            res = self.session.get("https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/loans/v2", headers=self.json_header)
            if res.status_code == 200:
                for material in res.json():
                    id = material['loanDetails']['recordId']
                    data = self._getDetails(id)
                    if data:
#                        data['CoverUrl'] = self._getCoverUrl(data['pid'])
                        # Create an instance of libraryLoan
                        obj = libraryLoan(data)

                        # Renewable
                        obj.renewId = material['loanDetails']['loanId']
                        obj.renewAble = material['isRenewable']
                        obj.loanDate = parser.parse(material['loanDetails']['loanDate'], ignoretz=True)
                        obj.expireDate = parser.parse(material['loanDetails']['dueDate'], ignoretz=True)
                        obj.id = material['loanDetails']['materialItemNumber']
                        loans.append(obj)

        # Ebooks
        res = self.session.get('https://pubhub-openplatform.dbc.dk/v1/user/loans', headers=self.json_header)
        if res.status_code == 200:
            edata = res.json()

            self.user.eBooks = edata['userData']['totalEbookLoans']
            self.user.eBooksQuota = edata['libraryData']['maxConcurrentEbookLoansPerBorrower']
            self.user.audioBooks = edata['userData']['totalAudioLoans']
            self.user.audioBooksQuota = edata['libraryData']['maxConcurrentAudiobookLoansPerBorrower']

            for material in edata['loans']:
                id = material['libraryBook']['identifier']
                res2 = self.session.get(f'https://pubhub-openplatform.dbc.dk/v1/products/{id}', headers=self.json_header)
                if res2.status_code == 200:
                    data = res2.json()['product']
                    obj = libraryLoan(data)

                    # Details
                    obj.id = id
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
    def fetchReservations(self, physical=[]):
        reservations = []
        reservationsReady = []

        # Physical books
        if self.use_national:
            for data in physical:
                _LOGGER.debug(data)
                if data['status'] == 'AVAILABLE_FOR_PICKUP':
                    obj = libraryReservationReady(data)
                else:
                    obj = libraryReservation(data)

                # Details
                obj.id = data['orderId']
                obj.createdDate = parser.parse(data['orderDate'], ignoretz=True)
                obj.pickupLibrary = data['pickUpBranch']['agencyName']
                if data['status'] == 'AVAILABLE_FOR_PICKUP':
    #                obj.reservationNumber = material['pickupNumber']
    #                obj.pickupDate = parser.parse(material['pickupDeadline'], ignoretz=True)
                    reservationsReady.append(obj)
                else:
                    if data['pickUpExpiryDate']:
                        obj.expireDate = parser.parse(data['pickUpExpiryDate'], ignoretz=True)
                    obj.queueNumber = data['holdQueuePosition']
                    reservations.append(obj)
        else:
            res = self.session.get("https://fbs-openplatform.dbc.dk/external/v1/agencyid/patrons/patronid/reservations/v2", headers=self.json_header)
            materials = {item['transactionId']: item for item in res.json()}  # make sure only to take last if more than one item with same transaction
            for material in materials.values():
                id = material['recordId']
                data = self._getDetails(id)
                if data:
#                    data['CoverUrl'] = self._getCoverUrl(data['pid'])
                    if material['state'] == 'readyForPickup':
                        obj = libraryReservationReady(data)
                    else:
                        obj = libraryReservation(data)

                    # Details
                    obj.id = id
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

        # eReolen
        res = self.session.get("https://pubhub-openplatform.dbc.dk/v1/user/reservations", headers=self.json_header)
        if res.status_code == 200:
            edata = res.json()
            for material in edata['reservations']:
                _LOGGER.debug(f"E-reol reservering data {material}")
                id = material['identifier']
                res2 = self.session.get(f'https://pubhub-openplatform.dbc.dk/v1/products/{id}', headers=self.json_header)
                if res2.status_code == 200:
                    data = res2.json()['product']
                    _LOGGER.debug(f"E-reol reservering data {res.json()}")

                    obj = libraryReservation(data)
                    obj.id = id

                    obj.expireDate = parser.parse(material['expectedRedeemDateUtc'])
                    obj.createdDate = parser.parse(material['createdDateUtc'])
                    obj.pickupLibrary = 'ereolen.dk'
                    reservations.append(obj)
        self.user.reservations = reservations
        self.user.reservationsReady = reservationsReady

    # Get debts, if any, from the Library
    def fetchDebts(self, json={}):
        if json == {}:
            params = {'includepaid': 'false', 'includenonpayable': 'true'}
            res = self.session.get("https://fbs-openplatform.dbc.dk/external/agencyid/patron/patronid/fees/v2", params=params, headers=self.json_header)
            if res.status_code == 200:
                json = res.json()
        debts = []
        for debt in json:
            # TODO more than one material?
            material = debt['materials'][0]
            id = material['recordId']
            data = self._getDetails(id)
            if data:
#                data['CoverUrl'] = self._getCoverUrl(data['pid'])
                obj = libraryDebt(data)

                obj.feeDate = parser.parse(debt['creationDate'], ignoretz=True)
                obj.feeDueDate = parser.parse(debt['dueDate'], ignoretz=True)
                obj.feeAmount = debt['amount']
                debts.append(obj)
        self.user.debts = debts
        self.user.debtsAmount = sum([float(obj.feeAmount) for obj in debts])


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
            elif 'manifestation' in data:
                # physical book
                self.coverUrl = data['manifestation']['cover']['thumbnail']
                self.title = data['manifestation']['titles']['full'][0] # or main
                if data['manifestation']['creators']:
                    self.creators = data['manifestation']['creators'][0]['display']
                self.type = data['manifestation']['materialTypes'][0]['materialTypeSpecific']['display']
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
    feeDate, feeDueDate, feeAmount = None, None, None
