#include <boost/chrono.hpp>
#include <boost/system/error_code.hpp>
#include <boost/thread.hpp>
#include <boost/version.hpp>

#include <iostream>

int main() {
    boost::system::error_code ec;
    int value = 0;
    boost::thread worker([&value]() { value = 42; });
    worker.join();
    boost::chrono::milliseconds elapsed(value);
    std::cout << "boost:" << BOOST_LIB_VERSION << ":" << elapsed.count() << ":"
              << ec.message() << "\n";
    return value == 42 ? 0 : 1;
}
