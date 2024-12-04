
def int_to_bytes(num, length=8):
    return num.to_bytes(length, "big")


def int_array(elements, size, default=0):
    array = [default] * size
    for i in range(len(elements)):
        array[i] = elements[i]
    bytes = b"".join(map(int_to_bytes, array))
    return bytes


def bytes_array(elements, size, default=b""):
    array = [default] * size
    for i in range(len(elements)):
        array[i] = elements[i]
    bytes = b"".join(array)
    return bytes
