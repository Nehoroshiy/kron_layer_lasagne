import time
import theano
import lasagne
import numpy as np

import theano.tensor as T

from utils import custom_sgd, iterate_minibatches
from kron_layer import KronLayer
from uv_kron_layer import UVKronLayer
from lowrank_layer import LowRankLayer


def build_custom_mlp(input_var=None, widths=None, drop_input=.2,
                     drop_hidden=.5, type="dense", params=None):
    # By default, this creates the same network as `build_mlp`, but it can be
    # customized with respect to the number and size of hidden layers. This
    # mostly showcases how creating a network in Python code can be a lot more
    # flexible than a configuration file. Note that to make the code easier,
    # all the layers are just called `network` -- there is no need to give them
    # different names if all we return is the last one we created anyway; we
    # just used different names above for clarity.

    params = params if params is not None else {}
    widths = widths if widths is not None else [100, 100]
    manifolds = {}

    # Input layer and dropout (with shortcut `dropout` for `DropoutLayer`):
    network = lasagne.layers.InputLayer(shape=(None, 1, 28, 28),
                                        input_var=input_var)
    if drop_input:
        network = lasagne.layers.dropout(network, p=drop_input)
    # Hidden layers and dropout:
    nonlin = lasagne.nonlinearities.rectify

    DEFAULT_RANK = 10
    DEFAULT_USE_RANK = True
    DEFAULT_PARAM_DENSITY = 0.2
    DEFAULT_SHAPE2 = (4, 4)

    # Convolutional layer with 32 kernels of size 5x5. Strided and padded
    # convolutions are supported as well; see the docstring.
    network = lasagne.layers.Conv2DLayer(
            network, num_filters=32, filter_size=(5, 5),
            stride=1, pad=2,
            nonlinearity=lasagne.nonlinearities.rectify,
            W=lasagne.init.GlorotUniform())
    # Expert note: Lasagne provides alternative convolutional layers that
    # override Theano's choice of which implementation to use; for details
    # please see http://lasagne.readthedocs.org/en/latest/user/tutorial.html.
    network = lasagne.layers.MaxPool2DLayer(network, pool_size=(2, 2))

    # Another convolution with 32 5x5 kernels, and another 2x2 pooling:
    network = lasagne.layers.Conv2DLayer(
            network, num_filters=64, filter_size=(5, 5),
            stride=1, pad=2,
            nonlinearity=lasagne.nonlinearities.rectify)
    network = lasagne.layers.MaxPool2DLayer(network, pool_size=(2, 2))

    if type == "dense":
        network = lasagne.layers.DenseLayer(
            network, widths[0], nonlinearity=nonlin)
    else:
        param_density = params.get('param_density', DEFAULT_PARAM_DENSITY)
        rank = params.get('rank', DEFAULT_RANK)
        use_rank = params.get('use_rank', DEFAULT_USE_RANK)
        shape2 = params.get('shape2', DEFAULT_SHAPE2)
        if type == "lowrank":
            network = LowRankLayer(network,
                                   widths[0],
                                   param_density=param_density,
                                   rank=rank,
                                   use_rank=use_rank,
                                   name="fixedrank0")
            manifolds["fixedrank0"] = network.manifold
        elif type == "uv_kron":
            network = UVKronLayer(network,
                                  widths[0],
                                  shape2=shape2,
                                  param_density=param_density,
                                  rank=rank,
                                  use_rank=use_rank,
                                  name="uv0")
        elif type == "kron":
            network = KronLayer(network,
                                widths[0],
                                shape2=(4, 4),
                                param_density=param_density,
                                rank=rank,
                                use_rank=use_rank,
                                name="kron_fixedrank0")
            manifolds["kron_fixedrank0"] = network.manifold
        else:
            raise ValueError("type must be one of 3 variants: 'dense', 'lowrank' or 'kron'")
    for width in widths[1:]:
        network = lasagne.layers.DenseLayer(
                network, width, nonlinearity=nonlin)
        if drop_hidden:
            network = lasagne.layers.dropout(network, p=drop_hidden)
    # Output layer:
    softmax = lasagne.nonlinearities.softmax
    network = lasagne.layers.DenseLayer(network, 10, nonlinearity=softmax)
    return network, manifolds


def generate_train_acc(input_X=None, target_y=None, widths=None, type="dense", params=None):
    input_X = T.tensor4("X") if input_X is None else input_X
    target_y = T.vector("target Y integer", dtype='int32') if target_y is None else target_y
    widths = [100] if widths is None else widths
    params = {'rank': 10 } if params is None else params
    dense_output, manifolds = build_custom_mlp(input_X, widths=widths, type=type, params=params)

    y_predicted = lasagne.layers.get_output(dense_output)


    all_weights = lasagne.layers.get_all_params(dense_output)


    loss = lasagne.objectives.categorical_crossentropy(y_predicted,target_y).mean()
    accuracy = lasagne.objectives.categorical_accuracy(y_predicted,target_y).mean()

    updates_sgd = custom_sgd(loss, all_weights, learning_rate=0.01, manifolds=manifolds)


    train_fun = theano.function([input_X,target_y],[loss],updates=updates_sgd)
    accuracy_fun = theano.function([input_X,target_y],accuracy)
    return train_fun, accuracy_fun


def comparison(X_train,y_train,X_val,y_val,X_test,y_test, kron_params=None):
    import pickle
    kron_params = [{'rank': p} for p in np.arange(2, 5, 1)] if kron_params is None else kron_params
    num_epochs = 5

    batch_size = 100

    hidden_units = [4*4]

    trains, accs = generate_train_acc(widths=hidden_units, type="dense")
    trains, accs = list(zip(*([(trains, accs)]
                              + [generate_train_acc(widths=hidden_units, type="kron", params=kron_param) for kron_param in kron_params]
                              + [generate_train_acc(widths=hidden_units, type="uv_kron", params=kron_param) for kron_param in kron_params])))

    names = ["dense"] + ["kron({})".format(p.values()) for p in kron_params] + ["uv_kron({})".format(p.values()) for p in kron_params]
    results = {}

    for train, acc, name in zip(trains, accs, names):
        res = {}
        res["train_fun"] = train
        res["accuracy_fun"] = acc
        res["train_err"] = []
        res["train_acc"] = []
        res["epoch_times"] = []
        res["val_acc"] = []
        results[name] = res

    for epoch in range(num_epochs):
        for (res_name, res) in results.items():
            train_err = 0
            train_acc = 0
            train_batches = 0
            start_time = time.time()
            for batch in iterate_minibatches(X_train, y_train,batch_size):
                inputs, targets = batch
                train_err_batch, train_acc_batch= res["train_fun"](inputs, targets)
                train_err += train_err_batch
                train_acc += train_acc_batch
                train_batches += 1

            # And a full pass over the validation data:
            val_acc = 0
            val_batches = 0
            for batch in iterate_minibatches(X_val, y_val, batch_size):
                inputs, targets = batch
                val_acc += res["accuracy_fun"](inputs, targets)
                val_batches += 1

            # Then we print the results for this epoch:
            print("for {}".format(res_name))
            print("Epoch {} of {} took {:.3f}s".format(
                epoch + 1, num_epochs, time.time() - start_time))

            print("  training loss (in-iteration):\t\t{:.6f}".format(train_err / train_batches))
            print("  train accuracy:\t\t{:.2f} %".format(
                train_acc / train_batches * 100))
            print("  validation accuracy:\t\t{:.2f} %".format(
                val_acc / val_batches * 100))
            res["train_err"].append(train_err / train_batches)
            res["train_acc"].append(train_acc / train_batches * 100)
            res["val_acc"].append(val_acc / val_batches * 100)
    for res in results.values():
        res.pop('train_fun')
        res.pop('accuracy_fun')
    with open("comparative_history.dict", 'wb') as pickle_file:
        pickle.dump(results, pickle_file)


def run(X_train,y_train,X_val,y_val,X_test,y_test):
    import pickle
    import cProfile
    kron_params = [{'param_density': p} for p in np.linspace(0.0, 0.0, 1, endpoint=False)]
    num_epochs = 5

    batch_size = 100

    hidden_units = [100**2]

    trains, accs = list(zip(*([generate_train_acc(widths=hidden_units, type="old_kron", params=kron_param) for kron_param in kron_params])))

    names = ["old_kron({})".format(p.values()) for p in kron_params]
    results = {}

    for train, acc, name in zip(trains, accs, names):
        res = {}
        res["train_fun"] = train
        res["accuracy_fun"] = acc
        res["train_err"] = []
        res["train_acc"] = []
        res["epoch_times"] = []
        res["val_acc"] = []
        results[name] = res

    # Just profile if you need
    pr = cProfile.Profile()
    pr.enable()
    for epoch in range(num_epochs):
        for (res_name, res) in results.items():
            train_err = 0
            train_acc = 0
            train_batches = 0
            start_time = time.time()
            for batch in iterate_minibatches(X_train, y_train,batch_size):
                inputs, targets = batch
                train_err_batch, train_acc_batch= res["train_fun"](inputs, targets)
                train_err += train_err_batch
                train_acc += train_acc_batch
                train_batches += 1

            # And a full pass over the validation data:
            val_acc = 0
            val_batches = 0
            for batch in iterate_minibatches(X_val, y_val, batch_size):
                inputs, targets = batch
                val_acc += res["accuracy_fun"](inputs, targets)
                val_batches += 1

            # Then we print the results for this epoch:
            print("for {}".format(res_name))
            print("Epoch {} of {} took {:.3f}s".format(
                epoch + 1, num_epochs, time.time() - start_time))

            print("  training loss (in-iteration):\t\t{:.6f}".format(train_err / train_batches))
            print("  train accuracy:\t\t{:.2f} %".format(
                train_acc / train_batches * 100))
            print("  validation accuracy:\t\t{:.2f} %".format(
                val_acc / val_batches * 100))
            res["train_err"].append(train_err / train_batches)
            res["train_acc"].append(train_acc / train_batches * 100)
            res["val_acc"].append(val_acc / val_batches * 100)
    # Just profile if you need
    pr.disable()
    pr.print_stats(sort='cumtime')
    for res in results.values():
        res.pop('train_fun')
        res.pop('accuracy_fun')
    with open("comparative_history.dict", 'wb') as pickle_file:
        pickle.dump(results, pickle_file)




if __name__ == "__main__":
    from mnist import load_dataset
    X_train,y_train,X_val,y_val,X_test,y_test = load_dataset()
    print(X_train.shape,y_train.shape)

    comparison(X_train,y_train,X_val,y_val,X_test,y_test)