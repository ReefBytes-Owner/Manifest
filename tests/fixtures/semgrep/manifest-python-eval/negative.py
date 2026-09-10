import ast


def compute(expr: str):
    return ast.literal_eval(expr)
