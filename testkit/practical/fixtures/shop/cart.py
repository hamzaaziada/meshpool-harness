def line_total(price, qty, discount=0.0):
    return price * qty * (1 - discount)


def cart_total(lines, tax_rate=0.2):
    sub = sum(line_total(**l) for l in lines)
    return round(sub * (1 + tax_rate), 2)
