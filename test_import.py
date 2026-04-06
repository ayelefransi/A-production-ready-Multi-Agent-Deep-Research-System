try:
    import api.main
    print("success")
except Exception as e:
    import traceback
    with open("traceback_output.log", "w") as f:
        traceback.print_exc(file=f)
