from app.calculator import add
def test_add():
    assert add(2, 3) == 5
    
def test_subtract():
    assert subtract(5, 3) == 2

def test_multiply():
    assert multiply(3, 4) == 12

def test_divide():
    assert divide(10, 2) == 5

def test_divide_by_zero():
    try:
        divide(10, 0)
        assert False
    except ValueError:
        assert True

