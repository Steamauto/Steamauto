def is_subsequence(s, t):
    t_index = 0
    s_index = 0
    while t_index < len(t) and s_index < len(s):
        if s[s_index] == t[t_index]:
            s_index += 1
        t_index += 1
    return s_index == len(s)
