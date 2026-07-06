from app.calculator import add, subtract, multiply, divide

def run():
    print("Simple Calculator App")
    print("Add:", add(5, 3))
    print("Subtract:", subtract(5, 3))
    print("Multiply:", multiply(5, 3))
    print("Divide:", divide(10, 2))

if __name__ == "__main__":
    run()