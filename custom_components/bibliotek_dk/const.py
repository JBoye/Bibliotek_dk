CONF_AGENCY = "agency"
CONF_BRANCH_ID = "branchId"
CONF_HOST = "host"
CONF_MUNICIPALITY = "municipality"
CONF_NAME = "name"
CONF_PINCODE = "pincode"
CONF_SHOW_DEBTS = "show_debts"
CONF_SHOW_LOANS = "show_loans"
CONF_SHOW_RESERVATIONS = "show_reservations"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_USER_ID = "user_id"
CREDITS = "J-Lindvig & TermeHansen (https://github.com/TermeHansen/Bibliotek_dk)"

DOMAIN = "bibliotek_dk"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "da,en-US;q=0.9,en;q=0.8",
    "Dnt": "1",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.dk/",
}

JSON_HEADERS = {
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Content-Type": "application/json",
    'Accept': '*/*',
}

MUNICIPALITY_LOOKUP_URL = "https://api.dataforsyningen.dk/kommuner/reverse?x=LON&y=LAT"

UPDATE_INTERVAL = 60
URL_FALLBACK = "https://bibliotek.kk.dk"
URL_LOGIN = "/login"
URL_LOGIN_PAGE = "/login?current-path=/user/me/dashboard"


details_query = '''
    query getManifestationViaMaterialByFaust($faust: String!) {
  manifestation(faust: $faust) {
    ...ManifestationBasicDetails
  }
}

    fragment ManifestationBasicDetails on Manifestation {
  pid
  titles {
    full
  }
  creators {
    display
  }
  cover {
    thumbnail
  }
  materialTypes {
    materialTypeSpecific {
      display
    }
  }
}
'''

status_query = '''
    query BasicUser {
      user {
        name
        mail
        address
        postalCode
        isCPRValidated
        loggedInAgencyId
        loggedInBranchId
        municipalityAgencyId
        agencies {
          id
          name
          type
          hitcount
          user {
            mail
          }
          result {
            branchId
            name
          }
        }
        debt {
            title
            amount
            creator
            date
            currency
            agencyId
        }
        loans {
          agencyId
          loanId
          dueDate
          title
          creator
          manifestation {
            pid
            ...manifestationTitleFragment
            ownerWork {
              workId
            }
            creators {
              ...creatorsFragment
            }
            materialTypes {
              ...materialTypesFragment
            }
            cover {
              thumbnail
            }
            recordCreationDate
          }
        }
        orders {
          orderId
          status
          pickUpBranch {
            agencyName
            agencyId
          }
          pickUpExpiryDate
          holdQueuePosition
          creator
          orderType
          orderDate
          title
          manifestation {
            pid
            ...manifestationTitleFragment
            ownerWork {
              workId
            }
            creators {
              ...creatorsFragment
            }
            materialTypes {
              ...materialTypesFragment
            }
            cover {
              thumbnail
            }
            recordCreationDate
          }
        }   
      }
    }
    fragment creatorsFragment on CreatorInterface {
  ... on Corporation {
    __typename
    display
    nameSort
    roles {
      function {
        plural
        singular
      }
      functionCode
    }
  }
  ... on Person {
    __typename
    display
    nameSort
    roles {
      function {
        plural
        singular
      }
      functionCode
    }
  }
}
    fragment manifestationTitleFragment on Manifestation {
  titles {
    main
    full
  }
}
    fragment materialTypesFragment on MaterialType {
  materialTypeGeneral {
    code
    display
  }
  materialTypeSpecific {
    code
    display
  }
}'''