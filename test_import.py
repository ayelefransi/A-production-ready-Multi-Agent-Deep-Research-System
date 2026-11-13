try:
    print("success")
except Exception:
    import traceback
    with open("traceback_output.log", "w") as f:
        traceback.print_exc(file=f)
