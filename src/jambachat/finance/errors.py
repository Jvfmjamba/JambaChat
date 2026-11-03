class FinanceError(Exception):
    pass


class FinanceUnavailableError(FinanceError):
    pass


class FinanceAuthError(FinanceError):
    pass
